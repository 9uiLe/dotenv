# Mac間でAIのナレッジを共有する

仕事と個人のMacで、共有してよいルール・スキル・Hook・Hook用スクリプトを再利用するためのリポジトリです。現在は、模擬環境だけで動作する試作品を実装しています。

## 利用方針

- 共通の内容はリポジトリ内の原本で編集する。
- 共有する内容とGitHubへ送るタイミングは自分で決める。会社・案件固有の内容は共有しない。
- 各Macは毎日**10:00（そのMacの現地時刻）**に受信を試み、成功時に反映する。
- 個人MacはCodex、仕事MacはCodexとClaude Codeを対象にする。
- 公開・送信・削除・費用発生・本物の設定変更は、具体的な内容を確認してから行う。

## 試作品を動かす

Python 3.11以上とGitを使い、このリポジトリの中で実行する。追加ライブラリは不要。

```sh
python3 prototype/demo.py
```

新しい模擬環境を `.prototype/validation-.../` に作り、次の流れを実行する。

1. 架空の共通ルール・スキル・Hook登録・Pythonスクリプトを、2台分の模擬ホームへ配る。
2. 配布済みHookを子プロセスで実行し、v1の出力を確認する。
3. 同日の重複更新を抑え、日時を翌日10:00へ進めてv2を受信する。
4. 更新と共有Hookを別々に停止・再開する。
5. 差分を確認して共通部分をv1へ戻し、Hookの出力もv1へ戻ることを確認する。
6. 共有部分だけを外し、既存の指示やHookを保持する。
7. 共有元の不正と書き込み途中の障害で、旧状態が保たれることを確認する。

GitHubの代わりにローカルのGitを使い、コミット取得から配布まで試す場合は次を実行する。

```sh
python3 prototype/git_demo.py
```

結果は生成したフォルダの `report.md` と `report.json` に残る。生成物はGit対象外で、再実行すれば新しく作成できる。検証後の削除対象は `.prototype/` に限定し、実装の `prototype/` は残す。[検証範囲](docs/VALIDATION.md)も参照。

## 操作を個別に試す

```sh
python3 -m prototype init
```

表示された `sandbox` のパスを、以下の `PATH` に指定する。`--pc personal` が個人、`--pc work` が仕事の模擬Mac。

```sh
python3 -m prototype --sandbox PATH --pc work sync --at 2026-10-20T10:00:00+09:00
python3 -m prototype --sandbox PATH --pc work status
```

| 操作 | 最後に指定するコマンド |
| --- | --- |
| 受信して差分だけ確認する | `preview-sync` |
| 更新を止める／再開する | `pause-sync` / `resume-sync` |
| 共有Hookを止める／再開する | `pause-hooks` / `resume-hooks` |
| 前の共有版への復元を確認する | `restore` |
| 確認した復元を模擬環境へ適用する | `restore --apply` |
| 導入取り消しを確認する | `uninstall` |
| 確認した導入取り消しを模擬環境へ適用する | `uninstall --apply` |

復元・導入取り消しは既定でプレビュー。適用後は更新と共有Hookの両方を止める。更新の再開だけではHookの停止を解除しない。`sync --force` は時刻と当日実行済み判定を省くが、更新停止を解除しない。

`source/git.json`がないsandboxは、従来のローカルデータ`source/bundle.json`を使う。Git受信を設定する場合は、共通引数の後へ次を指定する。

```sh
python3 -m prototype --sandbox PATH --pc work configure-git --url git@github.com:9uiLe/dotenv.git --branch master
python3 -m prototype --sandbox PATH --pc work preview-sync
```

設定の保存時は通信しない。`preview-sync`は取得用キャッシュだけを更新し、配布先・適用済み版・受信成功の記録を変えずに差分を示す。通常の`sync`は日次判定後に取得し、配布まで成功したときだけコミットを適用済みとして記録する。Gitへ送信する機能はない。

上の例はこの公開リポジトリの`master`を受信元にする設定。SSHでの受信には各Macの認証設定が必要で、受信器から実際のGitHubへ接続する試験は未実施。

## 共有する原本

`shared/manifest.json`が、版の名前・共通ルールのファイル・共有するスキルとスクリプトの一覧・Hook定義を指定する。本文は`shared/rules.md`、`shared/skills/`、`shared/hooks/`に置く。ここには架空の見本を用意しており、既存のPC設定を取り込んだものではない。

受信器は指定ブランチのコミットから、manifestに載った通常ファイルを読む。チェックアウト、受信したリポジトリのスクリプト実行、自動マージは行わない。前回成功コミットの子孫でない履歴や、不正な内容は拒否する。旧版へ手動復元しても、履歴変更を検出するための受信基準は巻き戻さない。

GitHub受信はHTTPS（公開リポジトリ）とSSHに対応する。SSHは既存のssh-agentと登録済みホスト鍵だけを使い、対話ログインや新しい鍵の登録はしない。資格情報ヘルパーを無効にしているため、HTTPSでの非公開リポジトリ認証は未対応。両方式とも今回の外部接続は未実施。

Gitの受信仕様は[fetch](https://git-scm.com/docs/git-fetch)、[履歴関係の判定](https://git-scm.com/docs/git-merge-base)、[通常blobの読み取り](https://git-scm.com/docs/git-cat-file)を参照。

## 実環境への差分案を読む

`preview-install`は指定したホームを読み取ってコピーし、別の模擬環境へ導入差分を保存する。コピーや差分はGit対象外のフォルダに本人だけが読める権限で保存する。元の設定へ書き込む機能はない。

```sh
python3 -m prototype --sandbox PATH --pc personal preview-install --read-home /Users/USER --source-url git@github.com:9uiLe/dotenv.git --source-branch master
```

`PATH`は模擬受信に使ったsandbox、`/Users/USER`は読取対象のホームに置き換える。差分はこの1台に対する個人用・仕事用の2案。もう一台の実際の差分ではない。本番用ランタイムとインストーラーが未提供のため、差分は適用不可の導入案として示す。

## 実装と検証の範囲

管理状態と旧版を保存するため、別の実行からも更新や復元を続けられる。配布先の共通部分や日次設定の直接編集を検出したら停止し、既存設定を上書きしない。追加した共有ファイルを外す場合は、削除せず退避する。Codexの既存Hookがconfig.toml内にある場合も、コメントや他の設定を保持して共存する。

Hookの例は、会話開始時に共有版を返すだけの単独Pythonスクリプト。配布済みファイルを原本と照合してから起動する。任意のスクリプトをOSで隔離する機能ではなく、実行したサンプルは通信・ファイル変更を行わない。

**本物の設定を書き換える機能やインストーラーはない。** 書き込み対象は専用マーカーのある `.prototype/` 内だけ。日次実行のplistも模擬ホーム内へ生成するだけで、OSには登録しない。現在の検証では日時を指定して日次判定を試しており、実際に翌日まで常駐して起動を待つ試験は行わない。

GitHub受信の処理は実装済みだが、外部接続・認証は未検証。AIアプリによる読み込み、Hookの信頼確認画面、スリープ復帰時の起動、本番への設定適用も未検証または未実装。[導入前の確認資料](docs/SETUP_PLAN.md)で、本番の変更対象・既存設定の保持方法・停止と復元の方針を示している。

## 再現できる検証

```sh
python3 -m unittest discover -s prototype -p 'test_*.py' -v
```

試験用フォルダも `.prototype/` に生成する。試験の責任は、更新・停止・復元の仕様、設定ファイルとHook起動の連携、CLIの通し操作に分けている。[CIとリポジトリの権限](docs/CI.md)も参照。
