# Open WebUI から AWS に接続する

Cursor と同様に「AWS に接続して」と依頼できるようにするため、  
**ホスト上のブリッジ**経由で `~/.aws` プロファイルと（必要なら）本番 DB トンネルを使います。

## ユースケース例（本番テナントの件数集計）

1. ブリッジ起動・ツール登録
   ```bash
   cd ~/git/local-llm-ops
   ./scripts/start-bridge.sh
   ./scripts/register-bridge-tools.sh
   ```
2. http://127.0.0.1:3080/ で **`ops-ja`**
3. 入力欄で **`#リポジトリ名`** を付ける（Knowledge）
4. ツールを有効化して依頼:
   > AWSに接続して、本番の〇〇の件数を集計して
5. ツール承認ダイアログが出たら内容を確認して許可（トンネル → SQL の順）

内部ではだいたい次を実行します。

- `list_db_connections` → `db.json` の connection 名
- `run_aws` で必要なら SSO login（プロファイルは `profiles/db.json` の `aws_profile`）
- `open_db_tunnel`（SSM から bastion/DB 情報 → SSH `-L`）
- `run_sql` で `COUNT` 等（テナント ID は Knowledge 推測せず実測）

接続定義: ローカルの `profiles/db.json`（gitignore。テンプレは `db.json.example`。秘密は SSM）。

## AWS プロファイルの設定と認識

このリポジトリにプロファイル定義は **置きません**。  
ホストの AWS CLI が見える名前だけを使います。

```
~/.aws/config (+ credentials / SSO)
        ↑
  aws configure list-profiles
        ↑
  ブリッジ GET /aws/profiles ＝ ツール list_aws_profiles
```

```bash
aws configure list-profiles
curl -s http://127.0.0.1:3091/aws/profiles
curl -s http://127.0.0.1:3091/db/connections
```

`profiles/db.json` の `aws_profile` がホストに無い・SSO 期限切れのときは、先に:

```bash
aws sso login --profile <your-profile>
```

## 構成

```
ブラウザ Open WebUI（Docker）
  → Tools: list_aws_profiles / run_aws / list_db_connections / open_db_tunnel / run_sql
  → http://host.docker.internal:3091
  → ホストの aws CLI + ssh + psql + ~/.aws
```

## 起動

```bash
cd ~/git/local-llm-ops
docker compose up -d
./scripts/start-bridge.sh
./scripts/register-bridge-tools.sh
./scripts/healthcheck.sh
```

前提: ホストに `aws` / `ssh` / `psql`（`brew install libpq` 等）。  
初回は `cp profiles/db.json.example profiles/db.json` して接続名を自分の環境に合わせる。

## 安全策

- ブリッジは `127.0.0.1` のみ待受
- `confirm=true` のときだけ実実行（既定は dry-run）
- SQL は参照系（SELECT 等）のみ
- 認証情報はホストの `~/.aws` と SSM。レスポンスはマスキング
- 個人が特定できる一覧は出さない運用（件数・集計）

## 停止

```bash
./scripts/start-bridge.sh stop
```
