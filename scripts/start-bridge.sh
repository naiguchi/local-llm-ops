#!/usr/bin/env bash
# ホスト側 AWS ブリッジをフォアグラウンド／バックグラウンド起動
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${OPS_BRIDGE_PORT:-3091}"
HOST="${OPS_BRIDGE_HOST:-127.0.0.1}"
PID_FILE="${OPS_BRIDGE_PID_FILE:-$ROOT/.bridge.pid}"
LOG_FILE="${OPS_BRIDGE_LOG_FILE:-$ROOT/.bridge.log}"

cd "$ROOT"

if [[ "${1:-}" == "stop" ]]; then
  if [[ -f "$PID_FILE" ]]; then
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "stopped bridge pid=$pid"
  else
    pkill -f 'agent/bridge/server.py' 2>/dev/null || true
    echo "stopped (no pid file)"
  fi
  exit 0
fi

if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  echo "already running: http://${HOST}:${PORT}/"
  exit 0
fi

if [[ "${1:-}" == "fg" ]]; then
  exec env PYTHONUNBUFFERED=1 python3 -u "$ROOT/agent/bridge/server.py"
fi

nohup env PYTHONUNBUFFERED=1 python3 -u "$ROOT/agent/bridge/server.py" >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 1.5
if curl -fsS "http://${HOST}:${PORT}/health" >/dev/null; then
  echo "OK: bridge http://${HOST}:${PORT}/ (pid $(cat "$PID_FILE"))"
else
  echo "FAIL: bridge 起動に失敗。ログ: $LOG_FILE" >&2
  tail -30 "$LOG_FILE" >&2 || true
  exit 1
fi
