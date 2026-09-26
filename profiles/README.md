## 接続プロファイル（DB / クライアント）

本番・ステージングへの接続情報・環境固有の設定は **Git に載せない**。

| ローカル実体（gitignore） | テンプレ（コミット可） |
| --- | --- |
| `profiles/db.json` | `profiles/db.json.example` |
| `profiles/repos.json` | `profiles/repos.json.example` |
| `profiles/knowledge-hints/{repo}-ops.md` | `profiles/knowledge-hint-ops.md.example` |
| `profiles/private/` | （任意のメモ） |

初回:

```bash
cp profiles/db.json.example profiles/db.json
cp profiles/repos.json.example profiles/repos.json
# 必要なら:
# mkdir -p profiles/knowledge-hints
# cp profiles/knowledge-hint-ops.md.example profiles/knowledge-hints/myapp-ops.md
```

- **Open WebUI からの DB 集計**: `db.json` にターゲット名と AWS プロファイル・SSM パスだけを書く  
  （パスワードは SSM。詳細は [../docs/aws-bridge.md](../docs/aws-bridge.md)）
- **端末の sql.sh**: TablePlus / `~/.pg_service.conf` のサービス名だけを渡す
- **knowledge-hints**: リポジトリに無い運用メモ（テナントの探し方・用語の訂正など）。索引時に Knowledge へ混入

## ローカルリポジトリ索引

`repos.json` で索引対象を制御します。

- **自動検出**: `auto_discover.enabled: true` で `~/git` 配下の Git をスキャン  
  （`defaults.include` に合うファイルがあるものだけ。名前の除外は `name_exclude`）
- **明示指定**: `repos[]` に path を書くと上書き・追加可能
- 画面ではリポジトリごとに `#名前` で選択

```bash
./scripts/index-repos.sh --dry-run   # 何が拾われるか確認
./scripts/index-repos.sh --reset     # Knowledge を作り直して投入
```

詳細: [../docs/local-repo-rag.md](../docs/local-repo-rag.md)
