# Mac間で共有するAI設定

Codex・Claude Codeへ配る指示ファイル・スキル・Hookの原本です。
導入・日次更新・停止・復元は、独立した[ai-knowledge CLI](https://github.com/9uiLe/ai-knowledge)が担当します。
このリポジトリには共有原本、その検証テスト、CI、コミット前フックを置きます。

## cloneせずに導入する

通常ユーザーでログインしたMacで、[uv](https://docs.astral.sh/uv/getting-started/installation/)とGitを用意します。
Python 3.11以上が必要です。uvに既存Pythonがなければ、対応するPythonを取得します。
`personal`はCodex、`work`はCodexとClaude Codeを対象にします。

```sh
uvx --from https://github.com/9uiLe/ai-knowledge/archive/refs/heads/main.tar.gz \
  ai-knowledge --profile personal plan
```

`report.md`と`changes.patch`を確認してから、表示された計画を適用します。

```sh
uvx --from https://github.com/9uiLe/ai-knowledge/archive/refs/heads/main.tar.gz \
  ai-knowledge apply --plan /表示された絶対パス/plan.json --apply
```

設定の原本をcloneしたり、PyPIへ公開したりする必要はありません。
初期受信元はこのリポジトリの`master`です。別の受信元は`plan --url ... --branch ...`で指定できます。
仕事用Macでは`personal`を`work`に置き換えます。運用・復元・対応範囲は
[CLIのREADME](https://github.com/9uiLe/ai-knowledge#readme)を参照してください。
CLIのコードが変わった場合、適用時の再照合で停止するので、計画を作り直します。
同じ版を確実に使うにはURLの`refs/heads/main`をCLIの完全なコミットSHAに置き換えます。

## 構成と編集場所

```text
shared/
  manifest.json          共有版・配置先・配布ファイル・Hook定義
  rules.md               共通ルール
  instructions/
    AGENTS.md            Codex専用の指示
    CLAUDE.md            Claude Code専用の指示
  skills/                スキル本体と補助ファイル
  hooks/                 会話開始時Hookとコミット前検査
tests/                  共有原本とコミット前検査のテスト
.githooks/pre-commit     このリポジトリのコミット前検査
.github/workflows/ci.yml 共有原本のCI
requirements-ci.txt     CI専用の検査ツール
```

普段編集するのは`shared/`です。現在の設定は`example-v1`の見本で、既存PCから取り込んだものではありません。
会社・案件固有の情報や認証情報は共有しません。公開するタイミングは自分で決めます。
CLIの実装と配布処理のテストは`ai-knowledge`側で管理します。

## 指示ファイルとスキルを登録する

manifestのパスは`shared/`からの相対パスです。`rules`を共通ルールとし、
`instructions`には必要なツールの専用指示を指定します。

| 原本 | Codex | Claude Code（work） |
| --- | --- | --- |
| `rules.md` | `~/.codex/AGENTS.md`の管理部分 | `~/.claude/CLAUDE.md`の管理部分 |
| `instructions/AGENTS.md` | 共通ルールの後に追記 | — |
| `instructions/CLAUDE.md` | — | 共通ルールの後に追記 |
| `skills/<name>/` | `~/.agents/skills/<name>/` | `~/.claude/skills/<name>/` |

既存の指示は残し、目印のある部分だけを更新します。スキルはコピーします。
`skills`はスキル名と配布先の対応で、`["claude"]`ならworkのClaude Codeだけに配ります。
プロファイルで無効なツールには配りません。

```json
{
  "schema": 1,
  "version": "example-v1",
  "rules": "rules.md",
  "instructions": {
    "codex": "instructions/AGENTS.md",
    "claude": "instructions/CLAUDE.md"
  },
  "skills": {
    "ai-knowledge-example": ["codex", "claude"]
  },
  "files": ["hooks/session_start.py", "hooks/token_limit.py"],
  "hooks": []
}
```

上記は配置項目の例です。実際の`hooks`には会話開始時Hookの定義があります。
`instructions`省略時は共通ルールだけ、`skills`省略時は`files`に明記したスキルを両ツールへ配ります。

スキルを追加する手順:

1. `shared/skills/<name>/SKILL.md`を作り、frontmatterに`name`と`description`を記載する。
2. `<name>`は小文字の英数字とハイフンで1〜64文字にし、frontmatterの`name`と一致させる。
   先頭・末尾・連続するハイフンは使わない。特定の接頭辞は不要。
3. `references/`や`scripts/`などの補助ファイルを同じスキル配下へ入れる。
4. manifestの`skills`に名前と対象ツールを登録する。配下の各ファイルを`files`に列挙する必要はない。
5. 原本を検証して公開し、CLIの`plan`で導入差分を確認する。

```sh
uvx --from https://github.com/9uiLe/ai-knowledge/archive/refs/heads/main.tar.gz \
  ai-knowledge validate shared
```

スキル一式はUTF-8テキストを対象とし、画像等のバイナリ・リンク・サブモジュールは拒否します。
各ファイルは1MiB以下、共有内容は合計5MiB以下、配布ファイルは1,024件以下です。
スクリプトは`python3 scripts/check.py`のようにインタープリターで起動します。
Claude Code向けには予約名`synced`と`anthropic-skills`を使えません。
同名の未管理スキルは上書きしません。配布先からツールを外した場合は、そのツールの管理ファイルだけを退避します。
原本からファイルそのものを削除した場合は自動削除せず更新を止めるため、CLIの明示的な取り外しで扱います。

## コミット前の5,000トークン制限

ステージ済みの追加・変更・リネーム・種別変更された通常ファイルを検査し、
**バイト数 / 4が5,000を超える場合（20,001バイト以上）**にコミットを止めます。
部分ステージもGitのインデックス内の内容で判定します。未ステージの内容・未変更ファイル・削除・リンク・
サブモジュールは対象にしません。

Gitから編集者がエージェントかどうかを確定できないため、手書きで管理するファイルを対象とし、
既知のロックファイル、`generated/`・`__generated__/`・`vendor/`・`node_modules/`配下、
既知の生成コード・圧縮済みコードを除外します。原本は`shared/hooks/token_limit.py`です。
それ以外の生成物や外部データはGit属性で明示します。

```gitattributes
api/client.py linguist-generated=true
third_party/** linguist-vendored
assets/logo.png token-limit-ignore
```

属性はステージ済みの`.gitattributes`を参照するので、その変更もステージしてください。
手書きのソースや文書の制限を回避するためには使いません。エラーには次の手順を表示します。

1. クラス・機能・責務ごとに別モジュールへ分割する。共通の型・補助関数・テストデータは専用ファイルへ、文書は章ごとに移す。
2. `import`・`include`・文書リンクを更新し、動作を確認する。
3. 各ファイルを20,000バイト以下にして、元ファイルと新ファイルを`git add`し、再度コミットする。

CLIはこの原本をPC共通フックへ配布します。既存フックの処理・引数・標準入力・終了コードも引き継ぎます。
個別の`core.hooksPath`が優先されるリポジトリでは、その`pre-commit`の先頭から検査専用の起動ファイルを呼びます。

```sh
"$HOME/.local/share/ai-knowledge/git-hooks/check-token-limit" || exit $?
```

このリポジトリ自身には同じ原本を呼ぶ`.githooks/pre-commit`があります。開発用cloneで有効にします。

```sh
git config --local core.hooksPath .githooks
```

## 開発と検証

原本の編集とテストを行う場合だけ、このリポジトリをcloneします。CLIはダウンロードして使えます。

```sh
uv run --no-project \
  --with https://github.com/9uiLe/ai-knowledge/archive/refs/heads/main.tar.gz \
  python -B -m unittest discover -s tests -p 'test_*.py' -v
```

共有原本の形式・Python構文・見本Hookの版と、実Gitを使ったコミット前検査を確認します。
CLI側のテストは、受信・導入・日次更新・既存設定保持・停止・復元・障害復旧・フックの継承を確認します。
双方のテストは本人の設定を変更しません。

`master`へのpushでCIを実行します。全Git履歴の秘密情報検査、Pythonのセキュリティ検査、
共有原本の検証、Linux/Python 3.11・macOS/Python 3.14の回帰試験を行います。
CIで使うCLIは完全なコミットSHAへ固定し、CLI側のCI成功を確認してから更新します。
各PCはこのリポジトリの受信コミットと一致するCIが成功した場合だけ共有原本を反映します。
