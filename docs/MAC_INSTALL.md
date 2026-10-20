# Macへの導入・停止・復元

導入用ランタイムとインストーラーを実装し、模擬環境と個人用Macで導入・停止・初期状態への復元・再導入を確認した。毎日10:00のLaunchAgent登録とOS経由の手動起動も確認済み。自然な定刻起動などの未確認項目は[検証範囲](VALIDATION.md)を参照。各Macへの適用は、生成した差分を確認してから実行する。

## 前提

- 通常ユーザーでログインしたMac。管理者権限やsudoは使わない。
- Python 3.11以上とGitが必要。ソフトの自動インストールはしない。
- 個人は`personal`（Codex）、仕事は`work`（CodexとClaude Code）を選ぶ。
- 標準の設定場所を使う。`CODEX_HOME`や`CLAUDE_CONFIG_DIR`で別の場所を使っている場合は、その場所を確認してから導入する。
- 共有元は公開GitHubのHTTPS。初期値は`https://github.com/9uiLe/dotenv.git`の`master`。受信には認証情報を保存しない。SSH・非公開リポジトリは本番導入の対象外。

Pythonの実行パスは導入時のものを保存する。そのPythonを削除・移動すると更新とHookを実行できなくなる。安定した配置を使い、Pythonの更新やランタイム自体の更新は別途レビューして行う。日次更新ではランタイムや依存ソフトを更新しない。

## 1. 導入差分を作成する

リポジトリ内で実行する。仕事用なら`personal`を`work`へ置き換える。

```sh
python3 -B -m ai_knowledge --profile personal plan
```

公開GitHubから共有ファイルを取得し、そのコミットのCI成功を確認する。設定は読み取るだけで、日次処理も登録しない。出力された次のファイルを確認する。

| ファイル | 内容 |
| --- | --- |
| `report.md` | 対象Mac・プロファイル・共有元コミット・変更するファイル一覧 |
| `changes.patch` | 既存設定の追記内容と配置するランタイムの全差分 |
| `plan.json` | 適用時に再照合する設定・共有内容・実行処理 |

生成先はGit対象外の`.prototype/mac-install-plan-.../`。フォルダは700、ファイルは600。既存設定を含むため、これらをGitHubへ送信しない。2台のMacそれぞれで差分を作り、一方の計画をもう一方へ持ち込まない。

## 2. 確認した差分を適用する

実設定を変更する操作なので、そのMacの差分を確認して初回導入を承認した後に行う。対象AIでの作業を終えてから実行する。

```sh
python3 -B -m ai_knowledge apply --plan /絶対パス/plan.json --apply
```

共有元・受信コミット・配布ランタイム・既存設定を再照合する。差分作成後にいずれかが変わっていたら、計画を作り直す。初回配布は時刻にかかわらず実行し、その日を更新成功日として記録する。

既存設定をバックアップし、共有部分と実行処理を同じ更新処理で配置してから、本人のLaunchAgentだけを登録する。登録失敗時は更新停止を試み、停止できたかも結果に表示する。`sync_stop_confirmed: false`の場合は、処理終了後に`pause-sync`を再試行して状態を確認する。

Codexの新規Hookの信頼確認はユーザー自身が行う。日次処理はその確認を自動承認しない。[Codex公式Hook仕様](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)

Codexが保存するHookの信頼記録を保持し、導入済みHookの登録形式を更新時にも維持する。共有Hook以外の信頼状態は変更しない。

## 導入で変わる場所

`~`は対象Macの本人のホーム。個別設定の全文置換はせず、共有部分だけを管理する。

| 対象 | 方法 |
| --- | --- |
| `~/.codex/AGENTS.md` | 目印を付けた共有部分だけ追記・更新 |
| `~/.agents/skills/ai-knowledge-*/` | 共有スキルを配置。同名が既にあれば停止 |
| `~/.codex/hooks.json` | 既存Hookを保持して共有登録を追加 |
| `~/.codex/config.toml` | 既存のinline Hookがある場合はこちらへ追記し、コメントや他の設定を保持 |
| `~/.claude/CLAUDE.md` | workだけ、共有部分を追記 |
| `~/.claude/skills/ai-knowledge-*/` | workだけ、共有スキルを配置 |
| `~/.claude/settings.json` | workだけ、既存設定・既存Hookを保持して共有登録を追加 |
| `~/.gitconfig` | トークン検査を含む共有版では専用includeだけ追記。既存Git設定を保持 |
| `~/.local/share/ai-knowledge/git-config` と `git-hooks/` | PC共通のコミット前検査と既存Gitフックへの引き継ぎ |
| `~/.local/share/ai-knowledge/runtime/` | レビューしたPython実行処理をコピー。元のcheckoutに依存せず実行 |
| `~/.local/share/ai-knowledge/installation.json` | プロファイル・配置場所・実行処理のハッシュ |
| `~/.local/share/ai-knowledge/source/git.json` | 受信URL・ブランチ・manifest |
| `~/.local/share/ai-knowledge/`内の管理データ | 状態、専用Gitキャッシュ、共有版、バックアップ、退避先、短い実行ログ |
| `~/Library/LaunchAgents/local.ai-knowledge.sync.plist` | 日次10:00とログイン時の起動を登録 |

配置数は共有版とPCによって異なる。実行時の状態・バックアップ等も加わるため、実際の数と内容は各Macで生成した差分を正本にする。

PC共通Gitフックは他のリポジトリにも適用され、元のGitフックを保持して呼び出す。
個別の `core.hooksPath` があるプロジェクトでは個別設定が優先される。
[コミット前チェックの説明](PRE_COMMIT.md)で適用範囲と併用方法を確認する。

## 導入済みランタイムの更新

日次更新は共有ファイルだけを更新する。新しい配布機能を使うには、更新したリポジトリ内で
導入済みランタイムの差分を作り、表示された差分を確認して適用する。

```sh
python3 -B -m ai_knowledge --profile personal upgrade-runtime
python3 -B -m ai_knowledge apply --plan /表示された絶対パス/plan.json --apply
```

更新は導入情報に保存された旧ランタイムのハッシュで照合するため、旧版から移行できる。
共有版・履歴・停止状態・受信先・日次登録は保持する。差分確認後の変更や同期中の競合は拒否し、
適用中の書き込み失敗では旧ランタイムへ戻す。新しい共有版は通常の `sync` で受信する。
Gitフックを含む共有版が公開されてCIが成功するまでは、PC共通Git設定は追加されない。

管理フォルダは本人所有・本人だけアクセス可能にする。状態・導入情報・バックアップJSONは600。リンクや特殊ファイル、同名スキル、`AGENTS.override.md`、共有部分の直接編集を見つけたら停止する。JSONは整形により空白差分が生じる場合がある。

## 日次更新と状態確認

保存されたPythonと次の起動ファイルを使う。以下の`python3`は導入時と同じ実行ファイルに置き換える。

```sh
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" status
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" sync
```

毎日10:00（そのMacの現地時刻）に受信し、同日に成功済みなら何もしない。ログイン時も起動するが、10時前なら適用しない。スリープ中の予定はlaunchdの復帰時処理を使う設計で、電源が切れていた場合は次のログイン後、10時以降に当日分を処理する。実際のスリープ復帰は初回導入後の確認項目。[AppleのLaunchAgent仕様](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

受信したコミットと一致する`.github/workflows/ci.yml`の最新CIが成功した場合だけ反映する。通信失敗・CI待ち・CI失敗・履歴の巻き戻し・競合では現状を保持する。失敗は成功済みに数えず、手動sync・次回ログイン・翌日10:00に再試行できる。GitHub公開APIの制限でも更新を見送る。

ログは管理フォルダの`sync.log`と`sync-error.log`、状態は`status`で確認する。日次ログには設定の本文・会話・Hook入力を記録しない。管理状態は64MiBを上限とし、上限に達する更新は適用せず停止する。履歴を整理する場合は先に停止し、管理データを私有領域へ退避してから個別に対応する。

## 停止・再開

以下は上記起動ファイルの最後に指定する操作。

| 操作 | 結果 |
| --- | --- |
| `pause-sync` | 停止フラグを保存し、本人の同期用LaunchAgentを解除。配布済みルール・スキルは保持 |
| `resume-sync` | 日次設定を戻して登録。共有Hookの停止状態は変えない |
| `pause-hooks` | 会話用共有HookとPC共通のトークン検査を停止。既存Hookを保持 |
| `resume-hooks` | 共有Hookを再開 |

実行中の更新や共有Hookがロックを保持していたら、強制終了せず失敗を報告する。処理終了を待って停止を再試行する。OS登録解除が失敗しても、停止フラグが保存できていれば以後の受信は行わない。ログやバックアップの削除はしない。

## 復元・取り外し

```sh
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" restore
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" restore --apply
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" uninstall
python3 "$HOME/.local/share/ai-knowledge/runtime/ai_knowledge/entrypoint.py" uninstall --apply
```

`restore`は前の共有版、`uninstall`は共有部分の取り外し。引数なしでは差分表示だけで、`--apply`時は更新と共有Hookを止めてから実行する。配布したファイルは削除せず専用フォルダへ退避する。導入後に増えた個別設定、既存Hook、バックアップ、ランタイム、空の設定ファイルは保持する。

復元後、必要なら`resume-hooks`で共有Hookだけを再開して確認する。`resume-sync`は新しい共有版を再び受信するので、復元内容の確認後に行う。取り外し後の`resume-sync`は拒否する。初回は前の共有版の履歴がないため、導入前への復元には`uninstall`を使う。

取り外し後は`reinstall`で再導入差分を表示し、`reinstall --apply`で現在のGitHub共有元を再受信・CI確認して配置する。バックアップと履歴を保持し、共有Hookは停止のままにする。必要なら`resume-hooks`で明示的に再開する。ランタイム自体の更新は別途レビューして行う。

ランタイムの改変・Pythonの欠損で通常コマンドが使えない場合、OS登録の緊急解除は次で行う。実設定を変更するため、必要になった時点で対象を確認して実行する。

```sh
launchctl bootout "gui/$(id -u)/local.ai-knowledge.sync"
```

共有Hookはランタイムが検証できなければ処理を実行しない。設定から共有登録だけを外す場合も、全設定のバックアップによる上書きはせず、個別の差分を確認する。

## 模擬検証の再現

```sh
python3 -B -m unittest discover -s prototype -p 'test_*.py' -v
python3 -B -m ai_knowledge.demo
```

模擬ホームでは本番と同じインストール・配布・停止・復元処理と、コピー済みランタイムを使う。OS登録だけは模擬アダプターに置き換え、Gitはローカルの共有元を使う。CI成功確認は応答例で別途試験する。個人用Macでは実際のGitHub受信・CI確認、OS登録、Codexへの読み込みも確認済み。別のMacでの適用と読み込みは、そのMacで承認後に確認する。
