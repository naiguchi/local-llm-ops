# セキュリティ方針

## 目的

運用・本番関連の会話・SQL・接続メタデータ・結果断片を **クラウド LLM（Claude / Cursor クラウド等）に送らない**。

## やってよいこと

- ローカル Ollama + Open WebUI での調査・下書き
- マスキング済みスキーマ・症状・集計結果の共有
- 既存接続プロファイル名の参照（値は出さない）
- `agent/tools/*.sh` による **承認後** の接続・SQL 実行
- ホスト AWS ブリッジ経由の **ツール承認後** の AWS CLI 実行
- ローカルリポジトリの **許可パスのみ** を Knowledge に索引して SQL 草案

## やってはいけないこと

- 本番作業を Claude / Cursor クラウド Agent に任せる
- パスワード・JWT・接続文字列をプロンプトや Git に貼る
- `.env` / 秘密 / DB ダンプを Knowledge 索引に入れる
- Open WebUI を LAN／インターネットに公開する（既定は `127.0.0.1` のみ）
- 無承認での接続・破壊的 SQL / AWS 変更の自動実行
- `confirm=true` をユーザー確認なしに連打するようモデルに強いること

## 残るリスク（ローカルでも残る）

- 端末紛失・盗難 → FileVault 推奨
- 誤承認 → 破壊的操作は `MUTATE` 確認を必須にしている
- ローカル会話履歴 → 案件終了後に Open WebUI 履歴を削除する

## リポジトリに置かないもの

- 本番 `DATABASE_URL`
- 接続プロファイル本体・認証情報
- 環境固有の `profiles/db.json` / `profiles/repos.json` / `profiles/knowledge-hints/`（テンプレは `*.example`）
- 顧客名・テナント UUID・本番 SSM 実パスなど運用固有メモ
- DB ダンプ
- 会話エクスポート（必要な場合は Git 管理外）
