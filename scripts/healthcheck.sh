#!/usr/bin/env bash
# Ollama / Open WebUI の疎通確認
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${OPEN_WEBUI_PORT:-3080}"
if [[ -f "$ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$ROOT/.env"
  set +a
  PORT="${OPEN_WEBUI_PORT:-$PORT}"
fi

echo "=== Ollama ==="
if ! command -v ollama >/dev/null 2>&1; then
  echo "FAIL: ollama 未インストール"
  exit 1
fi

if ! curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null; then
  echo "FAIL: Ollama API に接続できない（ollama serve を起動してください）"
  exit 1
fi
echo "OK: Ollama API"
ollama list || true

echo
echo "=== Open WebUI ==="
if curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || curl -fsS -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/" | grep -qE '200|302|401'; then
  echo "OK: Open WebUI http://127.0.0.1:${PORT}/"
else
  echo "WARN: Open WebUI 未起動または未応答（docker compose up -d）"
fi

echo
echo "=== Host AWS bridge ==="
BRIDGE_PORT="${OPS_BRIDGE_PORT:-3091}"
if curl -fsS "http://127.0.0.1:${BRIDGE_PORT}/health" >/dev/null; then
  echo "OK: bridge http://127.0.0.1:${BRIDGE_PORT}/"
  curl -fsS "http://127.0.0.1:${BRIDGE_PORT}/aws/profiles" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("profiles:", ", ".join(d.get("profiles") or []) or "(none)")' || true
else
  echo "WARN: bridge 未起動（./scripts/start-bridge.sh）"
fi

echo
echo "healthcheck 完了"
