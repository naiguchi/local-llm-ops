#!/usr/bin/env python3
"""
ホスト上で動くローカル運用ブリッジ。
Open WebUI（Docker）から host.docker.internal 経由で呼ばれ、
Mac 上の AWS CLI / DB トンネル / 参照 SQL を人間承認のうえで実行する。

秘密情報はレスポンスに出さない。実行は confirm=true のときのみ。
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("OPS_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPS_BRIDGE_PORT", "3091"))
AWS_BIN = os.environ.get("AWS_BIN") or shutil.which("aws") or "/usr/local/bin/aws"
PSQL_BIN = os.environ.get("PSQL_BIN") or shutil.which("psql") or "/opt/homebrew/bin/psql"
DB_CONFIG = Path(os.environ.get("OPS_DB_CONFIG", str(ROOT / "profiles" / "db.json")))
MAX_OUTPUT = int(os.environ.get("OPS_BRIDGE_MAX_OUTPUT", "12000"))
DEFAULT_TIMEOUT = int(os.environ.get("OPS_BRIDGE_TIMEOUT", "120"))

SECRET_PATTERNS = [
    re.compile(r"(?i)(aws_secret_access_key|secret_access_key|session_token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(\"SecretAccessKey\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(?i)(\"SessionToken\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(?i)(password|passwd|token|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(postgresql://[^:\s]+:)([^@/\s]+)(@)"),
]

BLOCKED_SUBCOMMANDS = {"configure"}

# name -> {pid, key_path, local_port}
_TUNNELS: dict[str, dict[str, Any]] = {}
# name -> {dsn, expires_at}  （パスワード含む。レスポンスには出さない）
_DB_CREDS: dict[str, dict[str, Any]] = {}


def redact(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        if pat.groups >= 3:
            out = pat.sub(r"\1***REDACTED***\3", out)
        else:
            out = pat.sub("***REDACTED***", out)
    out = re.sub(
        r'(?i)("(?:SecretAccessKey|SessionToken|AccessKeyId)"\s*:\s*")([^"]+)(")',
        r"\1***REDACTED***\3",
        out,
    )
    return out


def list_profiles() -> list[str]:
    try:
        proc = subprocess.run(
            [AWS_BIN, "configure", "list-profiles"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def load_db_config() -> dict[str, Any]:
    if not DB_CONFIG.is_file():
        return {"connections": []}
    return json.loads(DB_CONFIG.read_text(encoding="utf-8"))


def get_connection(name: str) -> dict[str, Any]:
    cfg = load_db_config()
    for c in cfg.get("connections") or []:
        if c.get("name") == name:
            return c
    raise ValueError(f"未知の DB 接続: {name}")


def list_db_connections() -> list[dict[str, Any]]:
    out = []
    for c in load_db_config().get("connections") or []:
        name = c.get("name")
        tun = _TUNNELS.get(name or "")
        out.append(
            {
                "name": name,
                "label": c.get("label") or name,
                "aws_profile": c.get("aws_profile"),
                "local_port": (c.get("tunnel") or {}).get("local_port"),
                "tunnel_open": bool(tun and _pid_alive(tun.get("pid"))),
                "allow_mutate": bool((c.get("sql") or {}).get("allow_mutate")),
            }
        )
    return out


def _pid_alive(pid: Any) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def build_aws_command(profile: str, args: list[str]) -> list[str]:
    if not profile or not re.fullmatch(r"[A-Za-z0-9_./+=,@-]+", profile):
        raise ValueError("不正なプロファイル名です")
    if not args or not isinstance(args, list):
        raise ValueError("args は非空の配列で指定してください")
    cleaned: list[str] = []
    for a in args:
        if not isinstance(a, str) or a == "":
            raise ValueError("args の各要素は空でない文字列である必要があります")
        if any(ch in a for ch in ["\n", "\r", ";", "|", "&", "`", "$", "(", ")", "<", ">"]):
            raise ValueError(f"許可されない文字を含む引数があります: {a!r}")
        cleaned.append(a)
    if "--profile" in cleaned:
        raise ValueError("--profile はブリッジが付与します")
    head = cleaned[0].lstrip("-")
    if head in BLOCKED_SUBCOMMANDS:
        raise ValueError(f"サブコマンド '{head}' はブリッジ経由では実行できません")
    return [AWS_BIN, "--profile", profile, "--no-cli-pager", *cleaned]


def aws_ssm_get_params(profile: str, names: list[str]) -> dict[str, str]:
    cmd = [
        AWS_BIN,
        "--profile",
        profile,
        "--no-cli-pager",
        "ssm",
        "get-parameters",
        "--with-decryption",
        "--names",
        *names,
        "--output",
        "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise RuntimeError(redact(proc.stderr or proc.stdout or "ssm get-parameters failed"))
    data = json.loads(proc.stdout)
    out = {p["Name"]: p["Value"] for p in data.get("Parameters") or []}
    missing = [n for n in names if n not in out]
    if missing:
        raise RuntimeError(f"SSM パラメータ不足: {missing}")
    invalid = data.get("InvalidParameters") or []
    if invalid:
        raise RuntimeError(f"SSM InvalidParameters: {invalid}")
    return out


def close_tunnel(name: str) -> None:
    info = _TUNNELS.pop(name, None)
    _DB_CREDS.pop(name, None)
    if not info:
        return
    pid = info.get("pid")
    if pid and _pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
    key_path = info.get("key_path")
    if key_path and os.path.isfile(key_path):
        try:
            os.remove(key_path)
        except OSError:
            pass


def cleanup_all_tunnels() -> None:
    for name in list(_TUNNELS.keys()):
        close_tunnel(name)


atexit.register(cleanup_all_tunnels)


def open_db_tunnel(name: str) -> dict[str, Any]:
    conn = get_connection(name)
    profile = str(conn.get("aws_profile") or "").strip()
    tunnel = conn.get("tunnel") or {}
    params_map = tunnel.get("ssm_params") or {}
    local_port = int(tunnel.get("local_port") or 15432)
    bastion_user = str(tunnel.get("bastion_user") or "ec2-user")

    if not profile:
        raise ValueError("aws_profile が未設定です")
    if profile not in list_profiles():
        raise ValueError(f"未知の AWS プロファイル: {profile}")

    required = ["db_host", "db_port", "db_name", "db_user", "db_password", "bastion_key", "bastion_ip"]
    for k in required:
        if k not in params_map:
            raise ValueError(f"tunnel.ssm_params.{k} が必要です")

    # 既存トンネルを張り直す
    close_tunnel(name)

    values = aws_ssm_get_params(profile, [params_map[k] for k in required])
    db_host = values[params_map["db_host"]]
    db_port = values[params_map["db_port"]]
    db_name = values[params_map["db_name"]]
    db_user = values[params_map["db_user"]]
    db_password = values[params_map["db_password"]]
    bastion_key = values[params_map["bastion_key"]]
    bastion_ip = values[params_map["bastion_ip"]]

    key_file = tempfile.NamedTemporaryFile(prefix=f"ops-bastion-{name}-", suffix=".pem", delete=False)
    key_path = key_file.name
    key_file.write(bastion_key.encode("utf-8") if isinstance(bastion_key, str) else bastion_key)
    if not bastion_key.strip().endswith("\n"):
        key_file.write(b"\n")
    key_file.close()
    os.chmod(key_path, 0o600)

    # ポート占有プロセスを可能な範囲で解放（自前トンネル想定）
    try:
        subprocess.run(
            ["lsof", f"-tiTCP:{local_port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001
        pass

    ssh = shutil.which("ssh") or "/usr/bin/ssh"
    ssh_cmd = [
        ssh,
        "-i",
        key_path,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-N",
        "-L",
        f"127.0.0.1:{local_port}:{db_host}:{db_port}",
        f"{bastion_user}@{bastion_ip}",
    ]
    proc = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    # 起動待ち
    time.sleep(1.5)
    if proc.poll() is not None:
        err = proc.stderr.read() if proc.stderr else ""
        os.remove(key_path)
        raise RuntimeError(f"SSH トンネル起動失敗: {redact(err)[:500]}")

    _TUNNELS[name] = {"pid": proc.pid, "key_path": key_path, "local_port": local_port}
    # URL エンコードは簡易（特殊文字は quote）
    from urllib.parse import quote_plus

    dsn = (
        f"postgresql://{quote_plus(db_user)}:{quote_plus(db_password)}"
        f"@127.0.0.1:{local_port}/{db_name}?sslmode=prefer"
    )
    _DB_CREDS[name] = {"dsn": dsn, "opened_at": time.time()}

    return {
        "ok": True,
        "connection": name,
        "aws_profile": profile,
        "local_port": local_port,
        "message": "トンネルを開きました。続けて run_sql で参照クエリを実行できます。",
    }


def validate_select_sql(sql: str, allow_mutate: bool = False) -> str:
    text = sql.strip().rstrip(";")
    if not text:
        raise ValueError("sql が空です")
    if ";" in text:
        raise ValueError("複数ステートメントは禁止です")
    if any(ch in text for ch in ["`", "$(", "\\"]):
        raise ValueError("許可されない文字があります")
    head = re.split(r"\s+", text, maxsplit=1)[0].upper()
    # 現状は参照のみ（allow_mutate は将来用）
    _ = allow_mutate
    if head not in {"SELECT", "WITH", "EXPLAIN", "SHOW", "TABLE"}:
        raise ValueError("参照系（SELECT / WITH / EXPLAIN / SHOW）のみ許可です")
    upper = f" {text.upper()} "
    for bad in (" INSERT ", " UPDATE ", " DELETE ", " DROP ", " ALTER ", " TRUNCATE ", " GRANT ", " REVOKE "):
        if bad in upper:
            raise ValueError(f"禁止キーワードを含みます: {bad.strip()}")
    return text


def run_sql(name: str, sql: str) -> dict[str, Any]:
    conn = get_connection(name)
    allow_mutate = bool((conn.get("sql") or {}).get("allow_mutate"))
    clean = validate_select_sql(sql, allow_mutate)

    tun = _TUNNELS.get(name)
    creds = _DB_CREDS.get(name)
    if not tun or not _pid_alive(tun.get("pid")) or not creds:
        raise RuntimeError(
            f"トンネル未接続です。先に open_db_tunnel(connection={name}, confirm=true) を実行してください。"
        )

    if not os.path.isfile(PSQL_BIN) and shutil.which("psql") is None:
        raise RuntimeError("psql が見つかりません（brew install libpq 等）")

    psql = PSQL_BIN if os.path.isfile(PSQL_BIN) else shutil.which("psql")
    env = os.environ.copy()
    env["PGPASSWORD"] = ""  # DSN に含む
    cmd = [
        psql,
        creds["dsn"],
        "-v",
        "ON_ERROR_STOP=1",
        "-P",
        "pager=off",
        "-c",
        clean,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=DEFAULT_TIMEOUT,
        check=False,
        env=env,
    )
    return {
        "ok": proc.returncode == 0,
        "connection": name,
        "sql": clean,
        "exit_code": proc.returncode,
        "stdout": redact(proc.stdout or "")[:MAX_OUTPUT],
        "stderr": redact(proc.stderr or "")[:MAX_OUTPUT],
    }


OPENAPI: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "local-llm-ops host bridge",
        "version": "1.1.0",
        "description": (
            "ホスト Mac 上の AWS / DB トンネル / 参照 SQL を人間承認のうえで実行する。"
            "秘密は返さない。実実行には confirm=true が必要。"
        ),
    },
    "paths": {
        "/health": {
            "get": {
                "operationId": "health",
                "summary": "ブリッジの生存確認",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/aws/profiles": {
            "get": {
                "operationId": "list_aws_profiles",
                "summary": "利用可能な AWS プロファイル名一覧",
                "responses": {"200": {"description": "profiles"}},
            }
        },
        "/aws/run": {
            "post": {
                "operationId": "run_aws",
                "summary": "AWS CLI をホストで実行（confirm=true のときのみ）",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["profile", "args"],
                                "properties": {
                                    "profile": {"type": "string"},
                                    "args": {"type": "array", "items": {"type": "string"}},
                                    "confirm": {"type": "boolean", "default": False},
                                    "timeout_sec": {"type": "integer", "default": DEFAULT_TIMEOUT},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "result"}},
            }
        },
        "/db/connections": {
            "get": {
                "operationId": "list_db_connections",
                "summary": "許可された DB 接続ターゲット一覧（秘密なし）",
                "responses": {"200": {"description": "connections"}},
            }
        },
        "/db/tunnel": {
            "post": {
                "operationId": "open_db_tunnel",
                "summary": "AWS SSM+SSH で DB トンネルを開く（confirm=true のみ）",
                "description": (
                    "profiles/db.json の connection 名を指定。"
                    "まず confirm=false → 承認後 confirm=true。"
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["connection"],
                                "properties": {
                                    "connection": {
                                        "type": "string",
                                        "description": "db.json の connections[].name",
                                    },
                                    "confirm": {"type": "boolean", "default": False},
                                    "close": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": "true ならトンネルを閉じる",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "result"}},
            }
        },
        "/db/sql": {
            "post": {
                "operationId": "run_sql",
                "summary": "トンネル経由で参照 SQL を実行（confirm=true のみ）",
                "description": (
                    "SELECT/WITH のみ。個人情報の一覧は避け、COUNT 等の集計を優先。"
                    "例: connection=<db.json の name>, "
                    'sql="SELECT COUNT(*) FROM <table> WHERE ... AND is_delete=false"'
                ),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["connection", "sql"],
                                "properties": {
                                    "connection": {"type": "string"},
                                    "sql": {"type": "string"},
                                    "confirm": {"type": "boolean", "default": False},
                                },
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "result"}},
            }
        },
    },
}


class Handler(BaseHTTPRequestHandler):
    server_version = "local-llm-ops-bridge/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON オブジェクトが必要です")
        return data

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/health", "/"):
            self._send(
                200,
                {
                    "ok": True,
                    "aws_bin": AWS_BIN,
                    "psql_bin": PSQL_BIN if os.path.isfile(PSQL_BIN) else shutil.which("psql"),
                    "port": PORT,
                    "db_config": str(DB_CONFIG),
                },
            )
            return
        if path == "/openapi.json":
            self._send(200, OPENAPI)
            return
        if path == "/aws/profiles":
            profiles = list_profiles()
            self._send(200, {"profiles": profiles, "count": len(profiles)})
            return
        if path == "/db/connections":
            conns = list_db_connections()
            self._send(200, {"connections": conns, "count": len(conns)})
            return
        self._send(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/aws/run":
                self._handle_aws_run(data)
                return
            if path == "/db/tunnel":
                self._handle_db_tunnel(data)
                return
            if path == "/db/sql":
                self._handle_db_sql(data)
                return
            self._send(404, {"error": "not found", "path": path})
        except subprocess.TimeoutExpired:
            self._send(200, {"ok": False, "error": "タイムアウトしました"})
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"ok": False, "error": redact(str(exc))})

    def _handle_aws_run(self, data: dict[str, Any]) -> None:
        profile = str(data.get("profile") or "").strip()
        args = data.get("args") or []
        confirm = bool(data.get("confirm", False))
        timeout = max(5, min(int(data.get("timeout_sec") or DEFAULT_TIMEOUT), 600))
        cmd = build_aws_command(profile, args)
        planned = " ".join(cmd)
        profiles = list_profiles()
        if profile not in profiles:
            self._send(
                400,
                {
                    "ok": False,
                    "error": f"未知のプロファイル: {profile}",
                    "available_profiles": profiles,
                    "planned_command": planned,
                },
            )
            return
        if not confirm:
            self._send(
                200,
                {
                    "ok": True,
                    "dry_run": True,
                    "message": "未実行。承認後に confirm=true で再呼び出ししてください。",
                    "planned_command": planned,
                    "profile": profile,
                    "args": args,
                },
            )
            return
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        self._send(
            200,
            {
                "ok": proc.returncode == 0,
                "dry_run": False,
                "exit_code": proc.returncode,
                "planned_command": planned,
                "stdout": redact(proc.stdout or "")[:MAX_OUTPUT],
                "stderr": redact(proc.stderr or "")[:MAX_OUTPUT],
            },
        )

    def _handle_db_tunnel(self, data: dict[str, Any]) -> None:
        name = str(data.get("connection") or "").strip()
        confirm = bool(data.get("confirm", False))
        close = bool(data.get("close", False))
        if not name:
            raise ValueError("connection が必要です")
        conn = get_connection(name)
        if close:
            if not confirm:
                self._send(
                    200,
                    {
                        "ok": True,
                        "dry_run": True,
                        "message": "トンネルを閉じる計画です。confirm=true で実行。",
                        "connection": name,
                    },
                )
                return
            close_tunnel(name)
            self._send(200, {"ok": True, "closed": True, "connection": name})
            return
        plan = {
            "connection": name,
            "label": conn.get("label"),
            "aws_profile": conn.get("aws_profile"),
            "local_port": (conn.get("tunnel") or {}).get("local_port"),
            "action": "ssm get-parameters + ssh -L tunnel",
        }
        if not confirm:
            self._send(
                200,
                {
                    "ok": True,
                    "dry_run": True,
                    "message": "未実行。承認後に confirm=true でトンネルを開きます。",
                    "plan": plan,
                },
            )
            return
        result = open_db_tunnel(name)
        self._send(200, result)

    def _handle_db_sql(self, data: dict[str, Any]) -> None:
        name = str(data.get("connection") or "").strip()
        sql = str(data.get("sql") or "")
        confirm = bool(data.get("confirm", False))
        if not name:
            raise ValueError("connection が必要です")
        clean = validate_select_sql(sql, allow_mutate=False)
        if not confirm:
            self._send(
                200,
                {
                    "ok": True,
                    "dry_run": True,
                    "message": "未実行。承認後に confirm=true で実行してください。",
                    "connection": name,
                    "sql": clean,
                },
            )
            return
        self._send(200, run_sql(name, clean))


def main() -> None:
    if not os.path.isfile(AWS_BIN) and shutil.which("aws") is None:
        print("WARN: aws CLI が見つかりません。", file=sys.stderr)
    if not os.path.isfile(PSQL_BIN) and shutil.which("psql") is None:
        print("WARN: psql が見つかりません。", file=sys.stderr)
    if not DB_CONFIG.is_file():
        example = ROOT / "profiles" / "db.json.example"
        print(
            f"WARN: DB 設定がありません: {DB_CONFIG}\n"
            f"  初回: cp {example} {DB_CONFIG}",
            file=sys.stderr,
        )

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"local-llm-ops bridge listening on http://{HOST}:{PORT}/", flush=True)
    print(f"OpenAPI: http://{HOST}:{PORT}/openapi.json", flush=True)
    print(f"DB config: {DB_CONFIG}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutdown", flush=True)
        cleanup_all_tunnels()
        httpd.server_close()


if __name__ == "__main__":
    main()
