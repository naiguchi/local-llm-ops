# local-llm-ops

本番接続・SQL・運用調査など、**クラウド LLM に載せたくない作業**を、自マシンの Ollama + Open WebUI で行う環境です。

アプリ本体のリポジトリとは独立です。アプリコードは変更しません。

## できること

| 機能 | 概要 |
| --- | --- |
| ローカルチャット | Open WebUI → ホスト Ollama（既定モデル `ops-ja`） |
| リポジトリ学習（RAG） | 手元 Git を Knowledge に索引し、`#リポジトリ名` で仕様・SQL 草案 |
| AWS / 本番 DB | ホストブリッジ経由。ツール承認後にのみ実行 |
| CLI 承認ヘルパー | `agent/tools/*.sh` で接続・SQL を人間承認後に実行 |

会話・ツール入出力はクラウドに送りません。詳細は [docs/why-not-cursor-cloud.md](docs/why-not-cursor-cloud.md)。

## 前提

- macOS + Docker Desktop
- Homebrew（Ollama 用）
- AWS / DB を画面から使う場合: ホストに `aws` / `ssh` / `psql`

### 推奨スペック（Mac）

既定モデルは **Qwen2.5 14B**（`ops-ja`）。Apple Silicon の統合メモリ想定です。

| | スペック | 目安 |
| --- | --- | --- |
| **推奨** | Apple Silicon（M1 以降）+ **統一メモリ 32GB** | 14B + Docker + ブラウザを快適に |
| **最低** | Apple Silicon + **統一メモリ 16GB** | 14B 可。他アプリ併用でスワップしやすい |
| **メモリ不足時** | 同上 | `./scripts/pull-models.sh qwen2.5:7b`（品質は下がる） |

- **ディスク空き**: モデル＋埋め込み＋ Docker で **40GB 以上**
- **GPU ボード**: 不要（Metal / 統合メモリ）
- **ネットワーク**: 初回のモデル pull のみ。推論中はクラウド LLM へ送らない

## 初回セットアップ

```bash
cd ~/git/local-llm-ops

# 設定ファイル（gitignore。中身は自分の環境に合わせて編集）
cp .env.example .env
cp profiles/db.json.example profiles/db.json
cp profiles/repos.json.example profiles/repos.json
# 任意: mkdir -p profiles/knowledge-hints
#       cp profiles/knowledge-hint-ops.md.example profiles/knowledge-hints/myapp-ops.md

# Ollama
brew install ollama   # 未導入時
ollama serve          # またはメニューバーアプリ

# モデル（qwen2.5:14b + 日本語ラッパー + 埋め込み）
./scripts/pull-models.sh

# Open WebUI → 日本語モデル登録 → ブリッジ → Knowledge 索引
docker compose up -d
./scripts/configure-webui-ja.sh
./scripts/start-bridge.sh
./scripts/register-bridge-tools.sh
./scripts/index-repos.sh --dry-run   # 対象確認
./scripts/index-repos.sh             # 索引作成
./scripts/healthcheck.sh
```

| URL | 用途 |
| --- | --- |
| http://127.0.0.1:3080/ | Open WebUI（チャット時は **`ops-ja`** を選択） |
| http://127.0.0.1:11434/ | Ollama API |
| http://127.0.0.1:3091/ | ホスト AWS/DB ブリッジ |

プロファイルの置き方: [profiles/README.md](profiles/README.md)

## 日常の起動

```bash
ollama serve                          # 未起動なら
docker compose up -d
./scripts/start-bridge.sh
./scripts/register-bridge-tools.sh    # WebUI 再作成後やツール未登録時
./scripts/healthcheck.sh
```

停止:

```bash
./scripts/start-bridge.sh stop
docker compose down
# Ollama はメニューバーまたは pkill ollama 等
```

## 使い方（チャット）

1. http://127.0.0.1:3080/ でモデル **`ops-ja`**
2. 必要なら入力欄で **`#リポジトリ名`**（Knowledge）
3. AWS/DB を使うときはツールを有効化 → 承認ダイアログで内容を確認して許可

例:

- `#myapp` + 「この機能フラグのスキーマ上の名前は？」 → Knowledge から回答
- `#myapp` + 「本番で ON の件数は？」 → Knowledge で仕様 → DB ツールで実測
- 「AWS に接続して、本番の〇〇を集計して」 → トンネル → `SELECT`（承認付き）

SSO 期限切れ時:

```bash
aws sso login --profile <your-profile>
```

## リポジトリ索引（RAG）

設定は `profiles/repos.json`（画面からは不可）。同期は CLI のみ。

```bash
$EDITOR profiles/repos.json
./scripts/index-repos.sh --dry-run
./scripts/index-repos.sh --reset --repo myapp
```

- 既定で `~/git` 配下を自動検出（常時ウォッチはしない。再同期は上記を再実行）
- `.env` / secrets は除外設定済み。秘密やダンプを索引に入れない

詳細: [docs/local-repo-rag.md](docs/local-repo-rag.md)

## AWS / 本番 DB（画面）

接続定義は `profiles/db.json`（パスワードは SSM。秘密はこのリポジトリに置かない）。  
AWS プロファイルはホストの `~/.aws` のみ。

```bash
aws configure list-profiles
curl -s http://127.0.0.1:3091/aws/profiles
curl -s http://127.0.0.1:3091/db/connections
```

詳細: [docs/aws-bridge.md](docs/aws-bridge.md)

## CLI 承認ヘルパー（端末から）

画面ブリッジとは別経路。接続・SQL を人が承認してから実行します。

```bash
./agent/tools/connect.sh --profile prod-readonly --dry-run \
  --command 'psql "service=prod-readonly" -c "SELECT 1"'

./agent/tools/sql.sh --mode select \
  --command 'psql "service=prod-readonly" -c "SELECT 1"'

# 変更系は MUTATE 入力が必要
./agent/tools/sql.sh --mode mutate \
  --command 'psql "service=staging" -c "UPDATE ..."'
```

手順の全体像: [docs/prod-ops-playbook.md](docs/prod-ops-playbook.md)

## セキュリティ（要約）

- 本番関連の会話・SQL・結果は **ローカルのみ**（Claude / Cursor クラウド Agent には載せない）
- 接続・SQL・AWS は **人間承認後のみ** 実行
- Open WebUI は既定で `127.0.0.1` のみ（LAN / インターネット公開しない）
- 秘密・`db.json` / `repos.json` / `knowledge-hints` は Git に載せない

方針全文: [docs/security.md](docs/security.md)

## ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| [docs/security.md](docs/security.md) | セキュリティ方針 |
| [docs/prod-ops-playbook.md](docs/prod-ops-playbook.md) | 本番・運用の手順 |
| [docs/aws-bridge.md](docs/aws-bridge.md) | 画面からの AWS / DB |
| [docs/local-repo-rag.md](docs/local-repo-rag.md) | リポジトリ索引 |
| [docs/why-not-cursor-cloud.md](docs/why-not-cursor-cloud.md) | クラウドを使わない理由 |
| [profiles/README.md](profiles/README.md) | ローカル設定ファイル |
