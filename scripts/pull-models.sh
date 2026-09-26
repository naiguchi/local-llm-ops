#!/usr/bin/env bash
# 無料モデルを Ollama に pull し、日本語＋ツール向けラッパーを作成する
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 既定: ツール呼び出しに強い Qwen2.5 14B（無料・ローカル、16GB RAM 想定）
BASE_MODEL="${1:-qwen2.5:14b}"
JA_MODEL="${2:-qwen2.5-ja}"
MODELFILE="$ROOT/models/Modelfile.qwen2.5-ja"

# 互換: 旧 llama3.1 指定時は旧 Modelfile を使う
if [[ "$BASE_MODEL" == llama3.1* ]]; then
  JA_MODEL="${2:-llama3.1-ja}"
  MODELFILE="$ROOT/models/Modelfile.llama3.1-ja"
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama が見つかりません。先にインストールしてください（brew install ollama）。" >&2
  exit 1
fi

echo "pull: $BASE_MODEL"
ollama pull "$BASE_MODEL"

# RAG 用埋め込み（ローカルリポジトリ索引）
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
echo "pull: $EMBED_MODEL"
ollama pull "$EMBED_MODEL"

if [[ -f "$MODELFILE" ]]; then
  echo "create: $JA_MODEL（日本語 SYSTEM プロンプト）"
  ollama create "$JA_MODEL" -f "$MODELFILE"
else
  echo "WARN: $MODELFILE が無いためラッパー作成をスキップ" >&2
fi

echo "完了"
ollama list
