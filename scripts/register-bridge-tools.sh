#!/usr/bin/env bash
# Open WebUI にホストブリッジ（Python ツール + OpenAPI）を登録し ops-ja を更新する
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${OPEN_WEBUI_PORT:-3080}"
BRIDGE_PORT="${OPS_BRIDGE_PORT:-3091}"
BASE_URL="http://127.0.0.1:${PORT}"
BRIDGE_URL="${OPS_BRIDGE_DOCKER_URL:-http://host.docker.internal:${BRIDGE_PORT}}"
TOOL_ID="${OPS_WEBUI_TOOL_ID:-local_llm_ops_bridge}"
TOOL_FILE="$ROOT/agent/tools/webui_bridge_tool.py"

if [[ -f "$ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$ROOT/.env"
  set +a
  PORT="${OPEN_WEBUI_PORT:-$PORT}"
  BASE_URL="http://127.0.0.1:${PORT}"
fi

SYSTEM_PROMPT="$(cat "$ROOT/agent/prompts/ops-ja-system.txt")"

if ! curl -fsS "$BASE_URL/api/config" >/dev/null; then
  echo "FAIL: Open WebUI に接続できない（$BASE_URL）" >&2
  exit 1
fi

if ! curl -fsS "http://127.0.0.1:${BRIDGE_PORT}/health" >/dev/null; then
  echo "FAIL: ブリッジが起動していません。先に ./scripts/start-bridge.sh" >&2
  exit 1
fi

TOKEN="${OPEN_WEBUI_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  for payload in '{"email":"","password":""}' '{"email":"admin@localhost","password":"admin"}'; do
    if curl -fsS -X POST "$BASE_URL/api/v1/auths/signin" \
      -H 'Content-Type: application/json' \
      -d "$payload" >/tmp/local-llm-ops-signin.json 2>/dev/null; then
      TOKEN="$(python3 -c 'import json; print(json.load(open("/tmp/local-llm-ops-signin.json")).get("token",""))')"
      [[ -n "$TOKEN" ]] && break
    fi
  done
fi

if [[ -z "$TOKEN" ]]; then
  echo "FAIL: 管理トークンを取得できませんでした。" >&2
  exit 1
fi

AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# --- Python ツール（Ollama への tool schema 渡しが確実） ---
python3 - "$TOOL_ID" "$TOOL_FILE" <<'PY' >/tmp/local-llm-ops-webui-tool.json
import json, sys
from pathlib import Path
tool_id, path = sys.argv[1], Path(sys.argv[2])
content = path.read_text(encoding="utf-8")
print(json.dumps({
  "id": tool_id,
  "name": "local-llm-ops Bridge",
  "content": content,
  "meta": {
    "description": "ホスト AWS / DB トンネル / 参照 SQL（承認付き）",
  },
}, ensure_ascii=False))
PY

if curl -fsS "${AUTH[@]}" "$BASE_URL/api/v1/tools/id/${TOOL_ID}" >/dev/null 2>&1; then
  echo "update python tool: $TOOL_ID"
  curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/tools/id/${TOOL_ID}/update" \
    --data-binary @/tmp/local-llm-ops-webui-tool.json >/dev/null
else
  echo "create python tool: $TOOL_ID"
  curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/tools/create" \
    --data-binary @/tmp/local-llm-ops-webui-tool.json >/dev/null
fi

# 旧ツールも同内容で更新（既存チャットの選択と互換）
python3 - "$TOOL_FILE" <<'PY' >/tmp/local-llm-ops-webui-tool-legacy.json
import json, sys
from pathlib import Path
content = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({
  "id": "aws_host_bridge",
  "name": "AWS Host Bridge",
  "content": content,
  "meta": {"description": "ホスト AWS / DB（local-llm-ops ブリッジ）"},
}, ensure_ascii=False))
PY
if curl -fsS "${AUTH[@]}" "$BASE_URL/api/v1/tools/id/aws_host_bridge" >/dev/null 2>&1; then
  curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/tools/id/aws_host_bridge/update" \
    --data-binary @/tmp/local-llm-ops-webui-tool-legacy.json >/dev/null
  echo "updated legacy tool: aws_host_bridge"
fi

# --- OpenAPI tool server（併用） ---
CURRENT="$(curl -fsS "${AUTH[@]}" "$BASE_URL/api/v1/configs/tool_servers" || echo '{"TOOL_SERVER_CONNECTIONS":[]}')"
python3 - "$CURRENT" "$BRIDGE_URL" <<'PY' >/tmp/local-llm-ops-tool-servers.json
import json, sys
current = json.loads(sys.argv[1])
bridge_url = sys.argv[2]
conns = current.get("TOOL_SERVER_CONNECTIONS") or []
conns = [c for c in conns if not (isinstance(c, dict) and str(c.get("url","")).rstrip("/") == bridge_url.rstrip("/"))]
conns.append({
    "url": bridge_url,
    "path": "/openapi.json",
    "type": "openapi",
    "auth_type": "none",
    "key": "",
    "config": {"enable": True},
    "info": {"id": "local-llm-ops-bridge", "name": "local-llm-ops AWS/DB bridge"},
})
print(json.dumps({"TOOL_SERVER_CONNECTIONS": conns}, ensure_ascii=False))
PY
curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/configs/tool_servers" \
  --data-binary @/tmp/local-llm-ops-tool-servers.json >/dev/null
echo "registered openapi tool server: $BRIDGE_URL"

# --- ops-ja ---
python3 - "$SYSTEM_PROMPT" "$TOOL_ID" <<'PY' >/tmp/local-llm-ops-ops-ja.json
import json, sys
system, tool_id = sys.argv[1], sys.argv[2]
print(json.dumps({
  "id": "ops-ja",
  "name": "ops-ja",
  "base_model_id": "qwen2.5-ja:latest",
  "meta": {
    "description": "日本語優先・AWS/DBツール付きローカル運用（Qwen2.5 14B）",
    "profile_image_url": "/static/favicon.png",
    # builtin_tools=False: Notes/Calendar 等の組み込みツール注入を止める
    "capabilities": {
      "vision": False,
      "usage": True,
      "tools": True,
      "builtin_tools": False,
    },
    "toolIds": [tool_id],
  },
  "params": {
    "system": system,
    "function_calling": "native",
    "temperature": 0.1,
  }
}, ensure_ascii=False))
PY

if curl -fsS "${AUTH[@]}" "$BASE_URL/api/v1/models/model?id=ops-ja" >/dev/null 2>&1; then
  curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/models/model/update" \
    --data-binary @/tmp/local-llm-ops-ops-ja.json >/dev/null
  echo "updated model: ops-ja (qwen2.5-ja 14B + tools)"
else
  curl -fsS -X POST "${AUTH[@]}" "$BASE_URL/api/v1/models/create" \
    --data-binary @/tmp/local-llm-ops-ops-ja.json >/dev/null
  echo "created model: ops-ja"
fi

echo
echo "OK: 無料 Qwen2.5 14B + DB ツールを登録しました"
echo "  1. モデル ops-ja を選択（説明に Qwen2.5 14B と出る）"
echo "  2. ツールで「local-llm-ops Bridge」を ON（Available Tools に出ること）"
echo "  3. まず list_db_connections → open_db_tunnel → run_sql の順で依頼"
echo "  4. #リポジトリ名 で Knowledge を付ける（件数はツール結果のみ）"

# Notes/Calendar/Automations の組み込みツールが AWS/DB を妨害するため DB 上でも無効化
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'local-llm-ops-open-webui'; then
  docker exec local-llm-ops-open-webui python3 -c '
import json, sqlite3, time
con = sqlite3.connect("/app/backend/data/webui.db")
cur = con.cursor()
now = int(time.time())
for key in ("notes.enable", "calendar.enable", "automations.enable"):
    cur.execute(
        "INSERT INTO config(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(False), now),
    )
con.commit()
print("disabled notes/calendar/automations in webui.db")
' || true
fi
