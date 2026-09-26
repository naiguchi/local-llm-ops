#!/usr/bin/env bash
# SQL 実行ヘルパー（人間承認必須）
# 接続はプロファイル／既存クライアント前提。秘密は引数に書かない。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POLICY="$ROOT/agent/policy.yaml"

usage() {
  cat <<'EOF'
Usage:
  sql.sh --mode select|mutate --command <shell-command>
  sql.sh --mode select --dry-run --command <shell-command>

例:
  sql.sh --mode select --command 'psql "service=prod-readonly" -c "SELECT count(*) FROM stores"'
  sql.sh --mode mutate --command 'psql "service=staging" -c "UPDATE ..."'

注意:
  - select / mutate とも承認プロンプトあり
  - mutate は追加で MUTATE と入力が必要
  - Claude / Cursor クラウドでは使わない
EOF
}

MODE=""
COMMAND=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --command) COMMAND="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不明な引数: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$MODE" || -z "$COMMAND" ]]; then
  usage
  exit 1
fi

if [[ "$MODE" != "select" && "$MODE" != "mutate" ]]; then
  echo "--mode は select または mutate" >&2
  exit 1
fi

echo "=== local-llm-ops sql ==="
echo "policy: $POLICY"
echo "mode: $MODE"
echo "command: $COMMAND"
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] 実行しません。"
  exit 0
fi

read -r -p "この SQL コマンドを実行しますか？ [y/N] " ans
if [[ ! "$ans" =~ ^[yY]$ ]]; then
  echo "中止しました。"
  exit 1
fi

if [[ "$MODE" == "mutate" ]]; then
  read -r -p "変更系です。続行するには MUTATE と入力: " confirm
  if [[ "$confirm" != "MUTATE" ]]; then
    echo "確認フレーズ不一致のため中止しました。"
    exit 1
  fi
fi

echo "実行中..."
eval "$COMMAND"
