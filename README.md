# Mac間でAIの設定を共有する

複数のMacへ、共通ルール・スキル・HookとGitのコミット前検査を配るリポジトリです。
原本をここで編集・公開し、各MacはCIが成功した共有版を毎日10:00に受信します。
実行時の依存はPython 3.11以上とGitだけです。

## 構成と編集場所

```text
shared/                  各PCへ配る原本
  manifest.json          共有版・配布ファイル・Hook定義
  rules.md               共通ルール
  skills/                共通スキル
  hooks/                 会話開始時Hookとコミット前検査
ai_knowledge/            導入・受信・日次更新・停止・復元
tests/                   隔離環境での自動テストとfixture
.githooks/pre-commit     このリポジトリのコミット前検査
.github/workflows/ci.yml 公開する共有版の検証
requirements-ci.txt     CI専用依存の固定
README.md                導入と運用の説明
.gitignore               一時生成物の除外
```

普段編集するのは`shared/`です。ファイルの追加・削除やHookの変更は
`shared/manifest.json`にも反映します。現在のルール・スキル・会話開始時Hookは
`example-v1`の見本で、既存PCから取り込んだ設定ではありません。
配布済みの対象を共有元から削除した場合は、自動削除せず更新を停止します。
会社・案件固有の情報や認証情報は共有しません。公開するタイミングは自分で決めます。

## 初回導入

通常ユーザーでログインしたMacで実行します。`personal`はCodex、`work`は
CodexとClaude Codeを対象にします。標準のホームと設定場所を使い、
`CODEX_HOME`や`CLAUDE_CONFIG_DIR`による別の配置は対象外です。

```sh
git clone https://github.com/9uiLe/dotenv.git
cd dotenv
python3 -B -m ai_knowledge --profile personal plan
```

仕事用Macでは`personal`を`work`に置き換えます。受信元の初期値は
公開GitHubの`9uiLe/dotenv`、ブランチは`master`です。
`plan`は既存設定を読み取り、導入差分を表示された私有の一時フォルダへ保存します。
設定変更や日次処理の登録はまだ行いません。

| 出力 | 確認する内容 |
| --- | --- |
| `report.md` | 対象Mac・プロファイル・受信コミット・変更対象 |
| `changes.patch` | 既存設定への追記と配布処理の全差分 |
| `plan.json` | 適用時に照合する導入計画 |

これらは既存設定を含むため公開せず、そのMacだけで使います。
差分を確認し、対象のAIでの作業を終えてから適用します。

```sh
python3 -B -m ai_knowledge apply --plan /表示された絶対パス/plan.json --apply
```

共有元・受信コミット・配布処理・既存設定を再照合し、計画作成後の変化があれば拒否します。
初回配布は時刻にかかわらず実行します。Codexの新規Hookの信頼確認はユーザー自身で行います。

導入時のPythonの場所を保存するため、その実行ファイルを移動・削除しないでください。
配布処理は管理フォルダへコピーされ、元のcheckoutには依存しません。
日次更新の対象は`shared/`の共有内容で、配布処理やPython自体は自動更新しません。

## 管理する設定

既存設定を保持し、目印を付けた共有部分と専用ファイルだけを管理します。

| 場所（`~`は本人のホーム） | 内容 |
| --- | --- |
| `~/.codex/AGENTS.md`、`~/.agents/skills/ai-knowledge-*/` | Codexの共有ルールとスキル |
| `~/.codex/hooks.json`または`~/.codex/config.toml` | 既存形式に合わせた共有Hook登録 |
| `~/.claude/CLAUDE.md`、`~/.claude/skills/ai-knowledge-*/`、`~/.claude/settings.json` | workだけ、Claude Codeの共有設定 |
| `~/.gitconfig` | PC共通のGit設定への専用include |
| `~/.local/share/ai-knowledge/` | 配布処理、共有版、状態、Git設定・Hook、バックアップ、ログ |
| `~/Library/LaunchAgents/local.ai-knowledge.sync.plist` | 毎日10:00とログイン時の起動 |

本人所有・私有の管理領域を使い、シンボリックリンクや特殊ファイル、不適切な権限を拒否します。
同名スキル、`AGENTS.override.md`、管理する共有部分の直接編集を検出した場合も停止します。
個別設定を全文置換して競合を解消することはありません。

## 日次更新と操作

導入時と同じPythonで、配置済みの起動ファイルを使います。

```sh
task_runtime="$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py"
python3 "$task_runtime" status
python3 "$task_runtime" sync
```

各Macの現地時刻10:00以降、当日まだ成功していなければ受信します。
ログイン時も同じ日次判定を行います。受信コミットと一致するCIの最新実行が成功した場合だけ
反映し、通信失敗・CI待ち・CI失敗・履歴の巻き戻し・競合では前の共有版を保持します。
失敗は当日の成功に数えません。必要なら`sync`で再試行できます。

次の操作は`python3 "$task_runtime" 操作名`で実行します。

| 操作名 | 結果 |
| --- | --- |
| `status` | 共有版・停止状態・直近の結果・OS登録を表示 |
| `pause-sync` / `resume-sync` | 更新と日次登録を停止／再開 |
| `pause-hooks` / `resume-hooks` | 会話用共有HookとPC共通トークン検査を停止／再開 |
| `restore` | 前の共有版へ戻す差分を表示 |
| `uninstall` | 共有部分を取り外す差分を表示 |
| `reinstall` | 取り外した状態から再導入する差分を表示 |

`restore`・`uninstall`・`reinstall`は、差分を確認してから`--apply`を付けて適用します。

```sh
python3 "$task_runtime" restore --apply
python3 "$task_runtime" uninstall --apply
python3 "$task_runtime" reinstall --apply
```

復元・取り外しは更新と共有Hookを止めてから実行します。初回導入前へ戻す場合は
`uninstall`を使います。配布ファイルは退避し、既存設定・既存Hook・履歴・バックアップ・
配布処理を保持します。再導入時も受信とCI確認を行い、共有Hookは停止のままにします。
必要に応じて`resume-hooks`で再開してください。`resume-sync`だけでは共有Hookは再開しません。

更新中などでロックされていれば強制終了せず失敗を報告するので、処理終了後に再試行します。
OS登録失敗時の`sync_stop_confirmed: false`は更新停止も確認できていない状態です。
`pause-sync`を再実行して`status`を確認します。
ログは管理フォルダの`sync.log`と`sync-error.log`にあり、設定本文・会話・Hook入力を記録しません。
管理状態の上限は64MiBで、超過する更新は適用しません。

## コミット前の5,000トークン制限

ステージ済みの追加・変更・リネーム・種別変更された通常ファイルを検査し、
**バイト数 / 4が5,000を超える場合（20,001バイト以上）**にコミットを止めます。
部分ステージもGitのインデックス内の内容で判定し、未ステージの内容や未変更ファイル、
削除・シンボリックリンク・サブモジュールは対象にしません。

Gitから編集者がエージェントかどうかを確定できないため、手書きで管理するファイルを対象とし、
既知のロックファイル、`generated/`・`__generated__/`・`vendor/`・`node_modules/`配下、
生成コード・圧縮済みコードの既知の名前を除外します。具体的な規則は
`shared/hooks/token_limit.py`にあります。それ以外の生成物や外部データはGit属性で明示します。

```gitattributes
api/client.py linguist-generated=true
third_party/** linguist-vendored
assets/logo.png token-limit-ignore
```

属性はステージ済みの`.gitattributes`を参照するので、その変更もステージしてください。
手書きのソースや文書の制限を回避するためには使いません。

上限超過のエラーは対象とサイズに加えて、次の分割手順を表示します。

1. クラス・機能・責務ごとに別モジュールへ分割する。共通の型・補助関数・テストデータは専用ファイルへ、文書は章ごとに移す。
2. `import`・`include`・文書リンクを更新し、動作を確認する。
3. 各ファイルを20,000バイト以下にして、元ファイルと新ファイルを`git add`し、再度コミットする。

PC共通フックは既存のGitフックへ処理・引数・標準入力・終了コードを引き継ぎます。
リポジトリ個別の`core.hooksPath`がある場合はそちらが優先されるので、
その`pre-commit`の先頭から検査専用の起動ファイルを呼び出します。

```sh
"$HOME/.local/share/ai-knowledge/git-hooks/check-token-limit" || exit $?
```

この呼び出しは呼び出し元フックへ再帰しません。このリポジトリには同じ共有原本を呼ぶ
`.githooks/pre-commit`があり、クローンごとに次で有効化します。

```sh
git config --local core.hooksPath .githooks
```

## 開発と検証

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

テストは私有の一時フォルダに模擬ホームとGitリポジトリを作り、本人の設定を変更しません。
導入・受信・時刻判定・既存設定保持・停止・復元・障害復旧・配布済み処理の起動・
コミット前検査を確認します。OS登録とGitHub API応答は模擬化するため、
実機の自然な定刻起動やAIアプリでのHook実行までは自動テストで保証しません。

`master`へのpushでCIを実行します。全Git履歴の秘密情報検査、Pythonのセキュリティ検査、
共有manifestとHookの構文検証、Linux/Python 3.11・macOS/Python 3.14での回帰試験を行います。
Actionsは完全なコミットSHA、検査ツールと依存は固定し、CIの権限は読み取りだけです。
受信器はこのCIの成功を確認するので、公開前の作業ファイルは各PCへ配布されません。

受信時はmanifestに載る通常ファイルだけを読み、checkout・自動マージ・受信元リポジトリの
コード実行は行いません。配布済みHookと実行処理は整合性を確認してから起動します。
HookのOS隔離機能はないため、共有スクリプトも公開前に確認してください。
