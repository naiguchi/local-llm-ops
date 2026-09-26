#!/usr/bin/env bash
# 接続プロファイル名に基づく接続コマンド実行（人間承認必須）
# 秘密情報は引数やログに出さない。実際の接続解決は既存クライアント／環境に委ねる。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
POLICY="$ROOT/agent/policy.yaml"

usage() {
  cat <<'EOF'
Usage:
  connect.sh --profile <name> --command <shell-command>
  connect.sh --profile <name> --dry-run --command <shell-command>

例:
  connect.sh --profile prod-readonly --dry-run --command 'psql "service=prod-readonly" -c "SELECT 1"'
  connect.sh --profile prod-readonly --command 'psql "service=prod-readonly" -c "SELECT 1"'

注意:
  - 接続実行前に必ず y/N 確認がある
  - パスワードや接続文字列をこのスクリプトの引数に直接書かない
  - Claude / Cursor クラウドでは使わない（ローカル運用専用）
EOF
}

PROFILE=""
COMMAND=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --command) COMMAND="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不明な引数: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$PROFILE" || -z "$COMMAND" ]]; then
  usage
  exit 1
fi

echo "=== local-llm-ops connect ==="
echo "policy: $POLICY"
echo "profile: $PROFILE"
echo "command: $COMMAND"
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] 実行しません。"
  exit 0
fi

read -r -p "この接続コマンドを実行しますか？ [y/N] " ans
if [[ ! "$ans" =~ ^[yY]$ ]]; then
  echo "中止しました。"
  exit 1
fi

echo "実行中..."
# shellcheck disable=SC2086
eval "$COMMAND"
