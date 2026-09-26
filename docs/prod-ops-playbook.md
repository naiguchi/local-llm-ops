# 本番・運用プレイブック

## 基本方針

- 推論はローカル LLM のみ
- 接続・SQL 実行は AI がコマンドを用意し、**人間が承認してから** `agent/tools` で実行
- 秘密は接続プロファイル側。リポジトリやチャットに書かない

## 手順

1. **作業場所を決める**  
   Open WebUI（http://127.0.0.1:3080/）またはローカル端末。Cursor クラウドは使わない。

2. **接続コマンドを用意する**  
   プロファイル名のみ使う。例: `psql "service=prod-readonly" -c "SELECT 1"`

3. **接続を承認する**
   ```bash
   ./agent/tools/connect.sh --profile prod-readonly \
     --command 'psql "service=prod-readonly" -c "SELECT 1"'
   ```
   プロンプトで `y` を入力した場合のみ実行。

4. **SQL を作成する（ローカル LLM 可）**  
   既定は `SELECT`。`UPDATE` / `DELETE` / DDL は明示的な変更依頼時のみ。

5. **SQL を承認して実行する**
   ```bash
   ./agent/tools/sql.sh --mode select --command '...'
   # 変更系
   ./agent/tools/sql.sh --mode mutate --command '...'
   ```
   mutate は確認フレーズ `MUTATE` が必要。

6. **結果の扱い**  
   件数・集計・エラーメッセージ中心。個人が特定できる一覧はマスクする。

7. **終了処理**  
   Open WebUI の当該チャット履歴を削除する。

## チェックリスト（変更系の前）

- [ ] 対象環境は意図どおりか（staging / prod）
- [ ] `WHERE` があるか／影響件数を先に `SELECT` したか
- [ ] バックアップまたはロールバック手順があるか
- [ ] `MUTATE` 確認を自分が入力したか
