#!/usr/bin/env bash
# Open WebUI に日本語優先カスタムモデル ops-ja を登録する
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${OPEN_WEBUI_PORT:-3080}"
BASE_URL="http://127.0.0.1:${PORT}"
JA_MODEL="${JA_MODEL:-qwen2.5-ja:latest}"
CUSTOM_ID="${CUSTOM_ID:-ops-ja}"

SYSTEM_PROMPT="$(cat "$ROOT/agent/prompts/ops-ja-system.txt")"

if [[ -f "$ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$ROOT/.env"
  set +a
  PORT="${OPEN_WEBUI_PORT:-$PORT}"
  BASE_URL="http://127.0.0.1:${PORT}"
fi

if ! curl -fsS "$BASE_URL/api/config" >/dev/null; then
  echo "FAIL: Open WebUI に接続できない（$BASE_URL）" >&2
  exit 1
fi

TOKEN=""
for payload in '{"email":"","password":""}' '{"email":"admin@localhost","password":"admin"}'; do
  if curl -fsS -X POST "$BASE_URL/api/v1/auths/signin" \
    -H 'Content-Type: application/json' \
    -d "$payload" >/tmp/local-llm-ops-signin.json 2>/dev/null; then
    TOKEN="$(python3 -c 'import json; print(json.load(open("/tmp/local-llm-ops-signin.json")).get("token",""))')"
    [[ -n "$TOKEN" ]] && break
  fi
done

AUTH=()
if [[ -n "$TOKEN" ]]; then
  AUTH=(-H "Authorization: Bearer $TOKEN")
fi

python3 - "$CUSTOM_ID" "$JA_MODEL" "$SYSTEM_PROMPT" <<'PY' >/tmp/local-llm-ops-ops-ja.json
import json, sys
custom_id, ja_model, system = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
  "id": custom_id,
  "name": custom_id,
  "base_model_id": ja_model,
  "meta": {
    "description": "日本語優先・ツール対応ローカル運用（Qwen2.5）",
    "profile_image_url": "/static/favicon.png",
    "capabilities": {"vision": False, "usage": True, "tools": True}
  },
  "params": {"system": system}
}, ensure_ascii=False))
PY

if curl -fsS "${AUTH[@]}" "$BASE_URL/api/v1/models/model?id=${CUSTOM_ID}" >/dev/null 2>&1; then
  echo "update: $CUSTOM_ID"
  curl -fsS -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
    "$BASE_URL/api/v1/models/model/update" --data-binary @/tmp/local-llm-ops-ops-ja.json >/dev/null
else
  echo "create: $CUSTOM_ID"
  curl -fsS -X POST "${AUTH[@]}" -H 'Content-Type: application/json' \
    "$BASE_URL/api/v1/models/create" --data-binary @/tmp/local-llm-ops-ops-ja.json >/dev/null
fi

echo "OK: モデル $CUSTOM_ID（base=$JA_MODEL）を日本語優先で設定しました"
echo "Open WebUI でモデルに「$CUSTOM_ID」を選んでください → $BASE_URL/"
