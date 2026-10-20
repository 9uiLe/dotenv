# コミット前のファイルサイズ確認

エージェントが編集するファイルを読みやすく保つため、ステージ済みファイルが
5,000トークンを超えたらコミットを止める。トークン数は指定どおり
**バイト数 / 4** とし、20,000バイトまでは許可し、20,001バイトから拒否する。
日本語も文字数ではなく、Gitに保存されるバイト数で数える。

## PC共通の導入

Python 3.11以上とGitを使い、追加の依存は不要。
共有原本は `shared/hooks/token_limit.py` で、`shared/manifest.json` の配布対象になっている。
[Macへの導入手順](MAC_INSTALL.md)で初回導入すると、次の設定を配置する。

- `~/.gitconfig` に専用の `include.path` ブロックを追加する。既存設定は保持する。
- `~/.local/share/ai-knowledge/git-config` でPC共通の `core.hooksPath` を設定する。
- `~/.local/share/ai-knowledge/git-hooks/` にGitイベントごとの起動ファイルを配置する。

トークン検査は配布済み共有版から実行し、日次更新・旧版復元に追従する。
元のcheckoutを削除・移動しても動作する。検査前に導入済みランタイムと共有版の
整合性を確認し、検査エラーや更新との競合ではコミットを止める。

すでに旧ランタイムを導入済みのPCでは、リポジトリ内から先に更新差分を作る。

```sh
python3 -B -m ai_knowledge --profile personal upgrade-runtime
python3 -B -m ai_knowledge apply --plan /表示された絶対パス/plan.json --apply
```

更新後、新しい共有版を受信する日次の `sync` でGit共通設定が追加される。
ランタイム更新だけでは共有版やGit設定は変わらない。
共有元の公開・CI成功と各Macでの受信が必要で、作業中の未公開ファイルは配布されない。

## 既存フックとの併用と適用範囲

PC共通フックは既存の `core.hooksPath` があればその場所の同名フックへ引き継ぐ。
`includeIf` によるプロジェクトごとの設定も実行時に解決する。
未設定なら、そのリポジトリ本来の `.git/hooks/` へ引き継ぐ。
`commit-msg` など他のGitイベントの引数・標準入力・終了コードも引き継ぐ。
既存フックのファイルを上書きしない。

Gitの優先順位により、**リポジトリ個別の `core.hooksPath` はPC共通設定より優先される**。
Huskyなどで個別設定しているプロジェクトでは、その `pre-commit` の先頭から共通フックを
呼び出す。後続の既存フックへ再帰しないよう、共通処理だけを呼ぶ。

```sh
"$HOME/.local/share/ai-knowledge/git-hooks/check-token-limit" || exit $?
```

`check-token-limit` はトークン検査だけを実行するため、呼び出し元フックへ再帰しない。
設定を確認するには、各プロジェクト内で `git config --show-origin --get core.hooksPath` を使う。

このリポジトリでは個別の `.githooks/pre-commit` を維持し、同じ共有原本を直接実行する。
そのため、PC共通設定の有無や一時停止にかかわらず、このリポジトリの検査は有効になる。
クローンごとに次で有効化する。

```sh
git config --local core.hooksPath .githooks
```

このリポジトリで手動確認する場合:

```sh
.githooks/pre-commit
```

## 対象と除外

追加・変更・リネーム・ファイル種別変更された、ステージ済みの通常ファイル全体を
確認する。部分ステージ時もインデックス内の内容を判定する。未ステージの変更や
変更されていない既存ファイルは判定に影響しない。削除、シンボリックリンク、
サブモジュールは対象外。

Gitの情報だけで編集者がエージェントかどうかは確定できないため、手書きで管理する
ファイルを対象とし、機械生成物と外部由来のファイルを以下の規則で除外する。

- 既知のロックファイル名。例: `package-lock.json`、`pnpm-lock.yaml`、`yarn.lock`、
  `Cargo.lock`、`poetry.lock`、`uv.lock`、`Package.resolved`、`go.sum`。
  完全な一覧はフック内の `LOCKFILES` を参照。任意の `*.lock` 全体は除外しない。
- `generated/`、`__generated__/`、`vendor/`、`node_modules/` 配下（任意の階層）。
- `*.generated.*`、`*.g.*`、`*_pb2.py`、`*_pb2_grpc.py`、`*.pb.go`、
  `*.min.js`、`*.min.css`。
- Git属性 `token-limit-ignore`、`linguist-generated`、`linguist-vendored` の
  いずれかが設定済み、または値が `true` のファイル。

上記の名前に当てはまらない生成コード、外部データ、画像などは `.gitattributes` で
明示する。手書きのソースや文書を除外するためには使わない。

```gitattributes
api/client.py linguist-generated=true
third_party/** linguist-vendored
assets/logo.png token-limit-ignore
```

属性は **ステージ済みの `.gitattributes`** を参照するため、属性の追加・変更も
`git add .gitattributes` でステージする。Gitのローカル属性設定も通常どおり適用される。
この検査はGitのコミット時に動作し、会話開始時のHookとは別に起動する。

## 停止・復元・取り外し

配布済みランタイムの `pause-hooks` でPC共通のトークン検査を停止し、`resume-hooks` で
再開する。既存のGitフックは停止しない。`pause-sync` は配布済み検査を止めず、更新だけを止める。
`restore --apply` は旧共有版へ戻し、共有Hookを停止する。
検査のない旧版へ戻した場合や `uninstall --apply` では、共通登録を外して元のGit設定へ戻す。
詳細な操作は[Macへの導入手順](MAC_INSTALL.md)を参照。

## 上限を超えたとき

エラーには対象パス、バイト数、トークン数と、次の手順を表示する。

1. クラス・機能・責務ごとに別モジュールへ分割する。共通の型・補助関数・
   テストデータは専用ファイルへ移し、文書は章ごとに分ける。
2. `import`、`include`、文書のリンクを更新して動作を確認する。
3. 各ファイルを20,000バイト以下にし、元ファイルと新しいファイルを
   `git add` して、再度コミットする。

既存の大きなファイルも、変更をステージした時点で分割が必要になる。
未変更のファイルを一括して整理する必要はない。

## 検証

```sh
python3 -B -m unittest prototype.test_token_hook prototype.test_git_dispatch prototype.test_git_install prototype.test_runtime_upgrade -v
```

一時Gitリポジトリで、実際の `git commit` とフックの動作を確認する。
