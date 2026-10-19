# リポジトリの権限とCI

## GitHubでの運用

リポジトリはPublic、既定ブランチはmaster。既存・今後作成する全ブランチで作成・更新・削除を制限し、所有者9uiLeだけが常時バイパスできるルールを使う。PRの作成やCIの成功は、本人が直接pushするための条件にしない。

Issues・Wiki・ProjectsとPRの受け付けは無効。書き込み権限のある共同作業者、未処理の招待、Deploy keyがないことを2026-10-20に確認した。Publicの閲覧・clone・forkは可能であり、forkの編集によって元のリポジトリは変わらない。本人の認証情報を使う操作と本人による操作は、リポジトリ設定では区別できない。

GitHub ActionsはGitHub公式Actionsだけを許可し、完全なコミットSHAへの固定を必須にする。既定トークン権限は読み取りで、PR承認権限は与えない。Secret scanningとpush protectionは有効な状態を保持する。

ルール: [Owner-only branch changes](https://github.com/9uiLe/dotenv/rules/25420837)。仕様は[個人リポジトリの権限](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)、[Rulesets](https://docs.github.com/en/rest/repos/rules)、[Actionsの安全な利用](https://docs.github.com/en/actions/reference/security/secure-use)を参照。

## CIが確認する内容

`.github/workflows/ci.yml`はmasterへのpushでだけ実行する。

| 検査 | 担保する内容 |
| --- | --- |
| Gitleaks | 固定版・SHA256照合済みのツールで全Git履歴の秘密情報を検出。ログでは検出内容を完全に伏せる |
| Bandit | Pythonの全ルールを実行し、解析不能・中以上の重要度の指摘を失敗にする。低重要度は位置と識別子を表示する |
| 共有元の形式 | manifest、許可パス、通常ファイル、サイズ上限、HookのPython構文を確認 |
| 回帰試験 | 受信・10:00の日次判定・既存設定の保持・停止・復元・障害からの復旧・読取専用の差分作成を確認 |

試験環境はUbuntu 24.04/Python 3.11とmacOS 15/Python 3.14。実行時間を制限し、新しいpushがあれば古い実行を中止する。checkout後に認証情報を残さず、秘密情報や本物のAI設定は試験に使わない。

実行時の依存は標準ライブラリだけ。CI用の依存は`requirements-ci.txt`で直接・間接依存を固定する。Banditが低重要度として報告する子プロセス起動や試験のassertは、一括除外せず、パス制限・shellを使わない引数・失敗時復旧の動作試験と合わせて扱う。

CIはpush後に問題を検出する。masterへの登録前に不良コミットを止める仕組みではない。また、日次の受信器がCI成功を待つ機能はまだない。自動適用を実環境へ導入する前に、失敗したコミットを取り込まない扱いを決める必要がある。

## 手元での検証と公開範囲

2026-10-20にmacOS/Python 3.14で34テスト、共有manifestの検証、Gitleaks、Banditを実行し成功。秘密情報の検出なし、中・高重要度のBandit指摘なし。GitHub上の実行結果は[Actions](https://github.com/9uiLe/dotenv/actions/workflows/ci.yml)で確認できる。

共有機能の公開対象はCI定義、CI依存、Git除外設定、`prototype/`のPythonソースと試験、`shared/`の架空の共有例、利用・導入・検証の資料。`.prototype/`の既存設定のコピー・差分・バックアップ・試験結果は削除し、公開対象に含めない。

手元の試験は以下で再現できる。

```sh
python3 -B -m unittest discover -s prototype -p 'test_*.py' -v
```
