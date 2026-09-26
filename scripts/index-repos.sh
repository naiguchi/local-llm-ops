#!/usr/bin/env bash
# ローカルリポジトリを Open WebUI Knowledge に索引するラッパー
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/index-repos.py" "$@"
