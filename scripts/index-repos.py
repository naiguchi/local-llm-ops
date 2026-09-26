#!/usr/bin/env python3
"""
ローカルリポジトリを Open WebUI Knowledge に索引する。
既定ではリポジトリごとに Knowledge を作り、画面の # / Attach Knowledge で選べる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "profiles" / "repos.json"
WEBUI = os.environ.get("OPEN_WEBUI_URL", "http://127.0.0.1:3080")
UPLOAD_TIMEOUT = int(os.environ.get("INDEX_UPLOAD_TIMEOUT", "600"))
UPLOAD_RETRIES = int(os.environ.get("INDEX_UPLOAD_RETRIES", "3"))


def load_config(path: Path) -> dict:
    if not path.is_file():
        example = ROOT / "profiles" / "repos.json.example"
        raise SystemExit(
            f"設定がありません: {path}\n"
            f"初回は次を実行: cp {example} {path}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("config must be a JSON object")
    return data


def get_token() -> str:
    token = os.environ.get("OPEN_WEBUI_TOKEN", "").strip()
    if token:
        return token
    for payload in (b'{"email":"","password":""}', b'{"email":"admin@localhost","password":"admin"}'):
        req = urllib.request.Request(
            f"{WEBUI}/api/v1/auths/signin",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("token"):
                    return body["token"]
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("Open WebUI トークンを取得できません")


def api_json(token: str, method: str, path: str, payload: dict | None = None) -> dict | list:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{WEBUI}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def api_upload_bytes(token: str, filename: str, raw: bytes, content_type: str) -> dict:
    boundary = "----localllmopsboundary"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(raw)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{WEBUI}/api/v1/files/",
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def prepare_upload_payload(display: str, file_path: Path) -> tuple[str, bytes, str]:
    """Open WebUI が抽出しやすいよう、テキスト系は .md として送る。"""
    text = file_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError("empty file")
    ext = file_path.suffix.lower().lstrip(".") or "txt"
    lang = {"prisma": "prisma", "sql": "sql", "ts": "ts", "js": "js", "md": "markdown"}.get(ext, ext)
    if ext == "md":
        content = f"<!-- source: {display} -->\n\n{text}"
    else:
        content = f"# Source: `{display}`\n\n```{lang}\n{text}\n```\n"
    safe_name = display.replace("/", "__") + ".md"
    return safe_name, content.encode("utf-8"), "text/markdown; charset=utf-8"


EXCLUDE_DIR_NAMES = {
    "node_modules",
    ".git",
    ".pnpm-store",
    ".history",
    "dist",
    "build",
    ".next",
    "coverage",
    "tmp",
    "filestorage",
    "secrets",
    "__pycache__",
    ".turbo",
    ".vercel",
    "vendor",
    "storybook-static",
}


def match_globs(rel: str, patterns: list[str]) -> bool:
    import fnmatch

    p = PurePosixPath(rel)
    for pat in patterns:
        if p.match(pat) or PurePosixPath(p.name).match(pat):
            return True
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat):
            return True
        if "**/" in pat:
            simplified = pat.replace("**/", "").replace("/**", "").lstrip("/")
            if simplified and (simplified in rel.split("/") or fnmatch.fnmatch(rel, f"*{simplified}*")):
                pass
    return False


def should_exclude(rel: str, excludes: list[str]) -> bool:
    parts = rel.split("/")
    if any(part in EXCLUDE_DIR_NAMES for part in parts):
        return True
    name = parts[-1]
    if name.startswith(".env") or name.startswith("credentials"):
        return True
    if name.endswith(".lock"):
        return True
    return match_globs(rel, excludes)


def knowledge_name_for_repo(cfg: dict, repo_name: str) -> str:
    """画面の # で打ちやすい短い名前。"""
    prefix = (cfg.get("knowledge_prefix") or "").strip()
    if prefix:
        return f"{prefix}{repo_name}"
    return repo_name


def _name_allowed(name: str, includes: list[str], excludes: list[str]) -> bool:
    import fnmatch

    if any(fnmatch.fnmatch(name, pat) for pat in excludes):
        return False
    if not includes:
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in includes)


def discover_repos(cfg: dict) -> list[dict]:
    """親ディレクトリ直下の Git リポジトリを自動検出する。"""
    auto = cfg.get("auto_discover") or {}
    if not auto or not auto.get("enabled"):
        return []
    roots = auto.get("roots") or []
    if isinstance(roots, str):
        roots = [roots]
    name_includes = list(auto.get("name_include") or ["*"])
    name_excludes = list(auto.get("name_exclude") or [])
    # 自分自身は既定で除外
    name_excludes = list({*name_excludes, "local-llm-ops"})
    require_files = bool(auto.get("require_matching_files", True))
    defaults = cfg.get("defaults") or {}
    file_includes = defaults.get("include") or ["**/*"]
    file_excludes = defaults.get("exclude") or []
    found: list[dict] = []
    seen: set[str] = set()

    def repo_has_matching_files(root: Path) -> bool:
        # 既知パスだけ見る（巨大リポジトリの全走査はしない）
        candidates = [
            "README.md",
            "AGENTS.md",
            "CLAUDE.md",
            "prisma/schema.prisma",
            "packages/prisma/prisma/schema.prisma",
        ]
        for rel in candidates:
            p = root / rel
            if p.is_file() and match_globs(rel, file_includes) and not should_exclude(rel, file_excludes):
                return True
        docs = root / "docs"
        if docs.is_dir() and match_globs("docs/x.md", file_includes):
            for path in docs.rglob("*.md"):
                rel_parts = path.relative_to(root).parts
                if any(part in EXCLUDE_DIR_NAMES for part in rel_parts[:-1]):
                    continue
                rel = path.relative_to(root).as_posix()
                if should_exclude(rel, file_excludes):
                    continue
                if match_globs(rel, file_includes):
                    return True
                # 1件見つかれば十分
                break
        return False

    for root_s in roots:
        parent = Path(root_s).expanduser().resolve()
        if not parent.is_dir():
            print(f"WARN: auto_discover root missing: {parent}", file=sys.stderr)
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if not (child / ".git").exists():
                continue
            name = child.name
            if name in seen:
                continue
            if not _name_allowed(name, name_includes, name_excludes):
                continue
            if require_files and not repo_has_matching_files(child):
                continue
            seen.add(name)
            found.append({"name": name, "path": str(child)})
    return found


def resolve_repos(cfg: dict) -> list[dict]:
    """明示リスト + 自動検出をマージ（明示が優先）。"""
    by_name: dict[str, dict] = {}
    for repo in discover_repos(cfg):
        by_name[repo["name"]] = repo
    for repo in cfg.get("repos") or []:
        by_name[repo["name"]] = repo
    return list(by_name.values())


def _expand_brace_patterns(pat: str) -> list[str]:
    """簡易な {a,b} 展開（1段のみ）。"""
    import re

    m = re.search(r"\{([^{}]+)\}", pat)
    if not m:
        return [pat]
    pre, post = pat[: m.start()], pat[m.end() :]
    return [f"{pre}{part.strip()}{post}" for part in m.group(1).split(",") if part.strip()]


def iter_included_files(root: Path, includes: list[str], excludes: list[str]):
    """include glob を展開してファイルを列挙する（apps 配下のソースも対象）。"""
    seen: set[Path] = set()

    def accept(path: Path):
        if path in seen or not path.is_file():
            return None
        try:
            rel_parts = path.relative_to(root).parts
            rel = path.relative_to(root).as_posix()
        except ValueError:
            return None
        if any(part in EXCLUDE_DIR_NAMES for part in rel_parts[:-1]):
            return None
        if should_exclude(rel, excludes):
            return None
        seen.add(path)
        return path

    # 既定の定番パス（include に明示が無くても拾いたい最小セットは呼び出し側で include する）
    candidates: list[Path] = []

    for raw_pat in includes:
        for pat in _expand_brace_patterns(raw_pat):
            pat = pat.lstrip("/")
            if any(ch in pat for ch in "*?[]"):
                try:
                    matched = list(root.glob(pat))
                except ValueError:
                    matched = []
                candidates.extend(matched)
            else:
                candidates.append(root / pat)

    # docs を include している場合のフォールバック（古い設定互換）
    if any("docs" in p and p.endswith(".md") for p in includes):
        docs = root / "docs"
        if docs.is_dir() and not any(root.glob("docs/**/*.md")):
            for dirpath, dirnames, filenames in os.walk(docs):
                dirnames[:] = [
                    d for d in dirnames if d not in EXCLUDE_DIR_NAMES and not d.startswith(".")
                ]
                for fname in filenames:
                    if fname.endswith(".md"):
                        candidates.append(Path(dirpath) / fname)

    for cand in candidates:
        p = accept(cand)
        if p:
            yield p


def collect_files_by_repo(cfg: dict, *, only_repos: set[str] | None = None) -> dict[str, list[tuple[str, Path]]]:
    defaults = cfg.get("defaults") or {}
    max_file = int(cfg.get("max_file_bytes") or 512000)
    max_total = int(cfg.get("max_total_bytes") or 30000000)
    default_repo_max = int(
        defaults.get("max_total_bytes")
        or cfg.get("max_repo_bytes")
        or max_total
    )
    default_max_files = int(defaults.get("max_files") or 0) or None
    by_repo: dict[str, list[tuple[str, Path]]] = {}
    total = 0
    repos = resolve_repos(cfg)
    if only_repos:
        repos = [r for r in repos if r["name"] in only_repos]
        missing = only_repos - {r["name"] for r in repos}
        if missing:
            print(f"WARN: unknown --repo: {', '.join(sorted(missing))}", file=sys.stderr)
    print(f"repos resolved: {len(repos)} ({', '.join(r['name'] for r in repos)})")
    for repo in repos:
        name = repo["name"]
        root = Path(repo["path"]).expanduser().resolve()
        if not root.is_dir():
            print(f"WARN: skip missing repo {name}: {root}", file=sys.stderr)
            continue
        includes = repo.get("include") or defaults.get("include") or ["**/*"]
        excludes = list(defaults.get("exclude") or []) + list(repo.get("exclude") or [])
        repo_max_total = int(repo.get("max_total_bytes") or default_repo_max)
        repo_max_file = int(repo.get("max_file_bytes") or max_file)
        repo_max_files = repo.get("max_files")
        if repo_max_files is None:
            repo_max_files = default_max_files
        else:
            repo_max_files = int(repo_max_files) or None
        bucket: list[tuple[str, Path]] = []
        repo_bytes = 0
        for path in iter_included_files(root, includes, excludes):
            size = path.stat().st_size
            if size <= 0:
                continue
            rel = path.relative_to(root).as_posix()
            if size > repo_max_file:
                print(f"skip large: {name}/{rel} ({size} bytes)", file=sys.stderr)
                continue
            if repo_max_files is not None and len(bucket) >= repo_max_files:
                print(
                    f"WARN: max_files reached for {name}; stopping this repo "
                    f"({len(bucket)} files, {repo_bytes} bytes)",
                    file=sys.stderr,
                )
                break
            if total + size > max_total or repo_bytes + size > repo_max_total:
                print(
                    f"WARN: max_total_bytes reached for {name}; stopping this repo "
                    f"({len(bucket)} files, {repo_bytes} bytes)",
                    file=sys.stderr,
                )
                break
            total += size
            repo_bytes += size
            bucket.append((f"{name}/{rel}", path))
        # ローカル運用ヒント（gitignore: profiles/knowledge-hints/{name}-ops.md）
        hint = ROOT / "profiles" / "knowledge-hints" / f"{name}-ops.md"
        if hint.is_file():
            bucket.append((f"{name}/_ops-hint.md", hint))
            print(f"  + hint {hint.name} for {name}")
        by_repo[name] = bucket
        print(f"  collected {name}: {len(bucket)} files ({repo_bytes} bytes)")
    return by_repo


def list_knowledge(token: str) -> list[dict]:
    listing = api_json(token, "GET", "/api/v1/knowledge/")
    items = listing.get("items") if isinstance(listing, dict) else listing
    return list(items or [])


def ensure_knowledge(token: str, name: str, description: str) -> str:
    for item in list_knowledge(token):
        if item.get("name") == name:
            return item["id"]
    created = api_json(
        token,
        "POST",
        "/api/v1/knowledge/create",
        {"name": name, "description": description},
    )
    return created["id"]


def delete_knowledge_by_names(token: str, names: set[str]) -> None:
    for item in list_knowledge(token):
        if item.get("name") in names:
            kid = item["id"]
            try:
                api_json(token, "DELETE", f"/api/v1/knowledge/{kid}/delete")
                print(f"deleted knowledge {item.get('name')} ({kid})")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: delete failed: {exc}", file=sys.stderr)


def ensure_embedding_ollama(token: str) -> None:
    try:
        cur = api_json(token, "GET", "/api/v1/retrieval/embedding")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: embedding config read failed: {exc}", file=sys.stderr)
        return
    if cur.get("RAG_EMBEDDING_ENGINE") == "ollama" and "nomic-embed" in str(
        cur.get("RAG_EMBEDDING_MODEL", "")
    ):
        print("embedding: already ollama/nomic-embed-text")
        return
    payload = {
        "RAG_EMBEDDING_ENGINE": "ollama",
        "RAG_EMBEDDING_MODEL": "nomic-embed-text",
        "RAG_EMBEDDING_BATCH_SIZE": 1,
        "ENABLE_ASYNC_EMBEDDING": True,
        "RAG_EMBEDDING_CONCURRENT_REQUESTS": 0,
        "ollama_config": {
            "url": os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            "key": "",
        },
    }
    try:
        api_json(token, "POST", "/api/v1/retrieval/embedding/update", payload)
        print("embedding: set to ollama/nomic-embed-text")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: embedding update failed: {exc}", file=sys.stderr)


def attach_knowledges_to_model(
    token: str, model_id: str, knowledge_list: list[dict], *, replace: bool
) -> None:
    """モデルに Knowledge を紐付ける。replace=False なら既存を残してマージ。"""
    try:
        model = api_json(token, "GET", f"/api/v1/models/model?id={model_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: model get failed: {exc}", file=sys.stderr)
        return
    meta = model.get("meta") or {}
    existing = meta.get("knowledge") or []
    if replace:
        merged = list(knowledge_list)
    else:
        by_id = {k["id"]: k for k in existing if isinstance(k, dict) and k.get("id")}
        for k in knowledge_list:
            by_id[k["id"]] = k
        merged = list(by_id.values())
    meta["knowledge"] = merged
    caps = meta.get("capabilities") or {}
    caps["tools"] = True
    meta["capabilities"] = caps
    payload = {
        "id": model_id,
        "name": model.get("name") or model_id,
        "base_model_id": model.get("base_model_id"),
        "meta": meta,
        "params": model.get("params") or {},
    }
    try:
        api_json(token, "POST", "/api/v1/models/model/update", payload)
        names = ", ".join(k["name"] for k in merged)
        print(f"model {model_id}: knowledge attached ({names})")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: model update failed: {exc}", file=sys.stderr)


def upload_files_to_knowledge(
    token: str, kid: str, files: list[tuple[str, Path]]
) -> tuple[int, int]:
    uploaded = 0
    failed = 0
    for display, path in files:
        try:
            filename, raw, ctype = prepare_upload_payload(display, path)
            text = raw.decode("utf-8")

            def _one():
                file_info = api_upload_bytes(token, filename, raw, ctype)
                file_id = file_info.get("id")
                if not file_id:
                    raise RuntimeError(f"no file id: {file_info}")
                api_json(
                    token,
                    "POST",
                    f"/api/v1/files/{file_id}/data/content/update",
                    {"content": text},
                )
                api_json(
                    token,
                    "POST",
                    f"/api/v1/knowledge/{kid}/file/add",
                    {"file_id": file_id},
                )

            last_err: Exception | None = None
            for attempt in range(1, UPLOAD_RETRIES + 1):
                try:
                    _one()
                    last_err = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    if attempt >= UPLOAD_RETRIES:
                        break
                    wait = min(30, 2 ** attempt)
                    print(
                        f"retry {attempt}/{UPLOAD_RETRIES} {display}: {exc} (sleep {wait}s)",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
            if last_err is not None:
                raise last_err
            uploaded += 1
            print(f"OK {display}")
        except urllib.error.HTTPError as exc:
            failed += 1
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            print(f"FAIL {display}: HTTP {exc.code} {detail}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {display}: {exc}", file=sys.stderr)
    return uploaded, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Index local repos into Open WebUI Knowledge")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=os.environ.get("DEFAULT_MODEL", "ops-ja"))
    parser.add_argument("--reset", action="store_true", help="対象 Knowledge を作り直す")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="索引するリポジトリ名（複数指定可）。未指定なら設定上の全件",
    )
    parser.add_argument(
        "--attach-to-model",
        action="store_true",
        help="ops-ja に全リポジトリ Knowledge を常時紐付け（既定は付けない＝画面で選択）",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    only = set(args.repo) if args.repo else None
    by_repo = collect_files_by_repo(cfg, only_repos=only)
    total_files = sum(len(v) for v in by_repo.values())
    print(f"collected {total_files} files across {len(by_repo)} repos")
    for repo_name, files in by_repo.items():
        kn = knowledge_name_for_repo(cfg, repo_name)
        print(f"  [{kn}] {len(files)} files")
        for display, path in files[:10]:
            print(f"    - {display} ({path.stat().st_size} B)")
        if len(files) > 10:
            print(f"    ... and {len(files) - 10} more")
    if args.dry_run:
        return

    token = get_token()
    ensure_embedding_ollama(token)

    mode = (cfg.get("knowledge_mode") or "per_repo").strip()
    legacy_name = cfg.get("knowledge_name") or "local-repos"
    desc_tpl = cfg.get("knowledge_description") or "ローカルリポジトリ: {repo}"

    created: list[dict] = []

    if mode == "single":
        names_to_reset = {legacy_name}
        if args.reset:
            delete_knowledge_by_names(token, names_to_reset)
        kid = ensure_knowledge(token, legacy_name, desc_tpl.format(repo="all"))
        print(f"knowledge id: {kid} ({legacy_name})")
        all_files = [f for files in by_repo.values() for f in files]
        uploaded, failed = upload_files_to_knowledge(token, kid, all_files)
        print(f"done: uploaded={uploaded} failed={failed}")
        created.append({"id": kid, "name": legacy_name})
    else:
        # per_repo: 各リポジトリ = 1 Knowledge（画面で #repo名 で選択）
        target_names = {knowledge_name_for_repo(cfg, n) for n in by_repo}
        # 旧・一括 Knowledge も reset 時に掃除
        if args.reset:
            delete_knowledge_by_names(token, target_names | {legacy_name, "local-repos"})
        uploaded_total = 0
        failed_total = 0
        for repo_name, files in by_repo.items():
            kn = knowledge_name_for_repo(cfg, repo_name)
            desc = desc_tpl.format(repo=repo_name) if "{repo}" in desc_tpl else f"{desc_tpl} ({repo_name})"
            kid = ensure_knowledge(token, kn, desc)
            print(f"knowledge id: {kid} ({kn})")
            u, f = upload_files_to_knowledge(token, kid, files)
            uploaded_total += u
            failed_total += f
            created.append({"id": kid, "name": kn})
        print(f"done: uploaded={uploaded_total} failed={failed_total}")

    attach = args.attach_to_model or bool(cfg.get("attach_to_model"))
    if attach and created:
        attach_knowledges_to_model(token, args.model, created, replace=True)
        print(f"Open WebUI: {WEBUI}/  → モデル {args.model} に常時紐付け")
    else:
        # モデルから一括 Knowledge を外し、チャットで # 選択する運用に寄せる
        try:
            model = api_json(token, "GET", f"/api/v1/models/model?id={args.model}")
            meta = model.get("meta") or {}
            if meta.get("knowledge"):
                meta["knowledge"] = []
                api_json(
                    token,
                    "POST",
                    "/api/v1/models/model/update",
                    {
                        "id": args.model,
                        "name": model.get("name") or args.model,
                        "base_model_id": model.get("base_model_id"),
                        "meta": meta,
                        "params": model.get("params") or {},
                    },
                )
                print(f"model {args.model}: cleared permanent knowledge (use # in chat)")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: could not clear model knowledge: {exc}", file=sys.stderr)
        names = " / ".join(f"#{k['name']}" for k in created) or "(none)"
        print(f"Open WebUI: {WEBUI}/  → チャットで {names} を選択")


if __name__ == "__main__":
    main()
