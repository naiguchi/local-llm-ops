# ローカルリポジトリ索引

Open WebUI の **Knowledge（RAG）** に、手元の Git リポジトリから **Prisma・仕様 docs・主要ソース** を取り込み、  
`#リポジトリ名` を付けたチャットでローカル LLM（`ops-ja`）が **コード根拠付き**で仕様や SQL 草案に答えられるようにします。

クラウドには送りません（Ollama 埋め込み + ローカル Open WebUI）。

## セットアップ

```bash
cd ~/git/local-llm-ops   # 自分の clone パスに合わせて

# 埋め込みモデル（初回）
ollama pull nomic-embed-text

# 対象はローカルの profiles/repos.json（gitignore）
cp profiles/repos.json.example profiles/repos.json   # 初回のみ
$EDITOR profiles/repos.json

# 何が拾われるか確認
./scripts/index-repos.sh --dry-run
./scripts/index-repos.sh --dry-run --repo myapp

# 索引作成（Open WebUI 起動済み前提）
./scripts/index-repos.sh --reset --repo myapp
```

運用メモ（テナントの探し方など）は `profiles/knowledge-hints/{repo}-ops.md` に置く（gitignore）。  
テンプレ: `profiles/knowledge-hint-ops.md.example`。

## 自動取り込みについて

**常時ウォッチはしません。** `./scripts/index-repos.sh` を実行したときに索引します。

| 項目 | 意味 |
|------|------|
| `auto_discover.*` | `~/git` などから Git を自動検出 |
| `defaults.include` | 全リポジトリ共通（Prisma / docs / **TS・TSX** など） |
| `defaults.max_total_bytes` | 自動検出リポジトリ1件あたりのバイト上限（肥大化防止） |
| `defaults.max_files` | 自動検出リポジトリ1件あたりのファイル数上限（埋め込み時間の抑制） |
| `repos[]` | 明示設定（パス・個別 include。自動検出より優先） |
| `--repo NAME` | 指定リポジトリだけ索引 |

特定リポジトリを厚く取りたい場合は `repos[]` で `include` を絞る（ノイズの多い generated・テストは除外）。

## 使い方

1. http://127.0.0.1:3080/ で **`ops-ja`**
2. **`#リポジトリ名`**（または Attach Knowledge）
3. 仕様質問例: 「このフラグは何をする？スキーマ上の名前は？」
4. 件数質問は続けて DB ツール（トンネル → `run_sql`）で実測

## 注意

- 本番接続文字列・個人情報ダンプは入れない（`.env` / secrets は除外）
- 環境固有の `repos.json` / `knowledge-hints` / `db.json` は Git に載せない
- 索引は近似検索。取りこぼしがあれば `repos[].include` を足して再索引
- 埋め込みはホスト Ollama の `nomic-embed-text`
- ソースを増やしすぎると遅くなるので `max_total_bytes` に注意
