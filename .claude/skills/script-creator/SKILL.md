---
name: script-creator
description: scripts/cli/ 配下のシェルスクリプトをガイド付きで作成します。対話的な要件収集、agent-friendly-cli ドキュメントに基づく設計、ベストプラクティス参照、テンプレート生成、品質検証、連携登録までをカバーします。スクリプトを作りたい、シェルスクリプトを追加したい、CLI ツールを作成したい場合に使用してください。
allowed-tools: Read, Grep, Glob, Write, Edit, Bash(shellcheck *), Bash(chmod *), Bash(ls *), Bash(mkdir *), Task(shell-best-practices), Task(claude-instruction-reviewer)
---

# スクリプト作成 (script-creator)

## 概要

`scripts/cli/` 配下のシェルスクリプトを、プロジェクトのベストプラクティスと agent-friendly-cli 設計原則に基づいてガイド付きで作成する Skill。

1. **対話的な要件収集**: スクリプトの目的、入出力、呼び出し元を対話的に収集
2. **設計原則の適用**: `docs/agent-friendly-cli/` の設計原則に基づき、エージェントが安全に利用できるスクリプトを設計
3. **ベストプラクティス参照**: `Task(shell-best-practices)` で公式ドキュメントに基づく最適な実装方法を取得
4. **テンプレート生成**: メタデータヘッダー、エラーハンドリング、共通ライブラリを利用したスクリプトを生成
5. **品質検証**: shellcheck 実行、メタデータ完備チェック、agent-friendly 観点の確認
6. **連携登録ガイド**: 呼び出し元の Agent/Skill への登録手順を案内

## 使用方法

```text
/script-creator
/script-creator <スクリプトの目的>
```

引数が指定されていればステップ 1 をスキップし、ステップ 2 に進む。

## 実行フロー

### ステップ 1: スクリプトの目的を収集

**引数が指定されている場合**: その内容を「目的」として採用し、ステップ 2 に進む。

**引数が指定されていない場合**: AskUserQuestion で収集。

- question: "作成するスクリプトの目的を教えてください。何を自動化しますか？"
- header: "Purpose"
- multiSelect: false
- options:
  1. **ビルド・テスト系**: ビルド検証、テスト実行、CI/CD 関連の自動化
  2. **コード生成・変換系**: コード生成、フォーマット変換、テンプレート展開
  3. **環境セットアップ系**: 開発環境の構築、worktree 管理、依存関係セットアップ
  4. **運用・メンテナンス系**: クリーンアップ、ログ分析、デプロイ補助

ユーザーが選択肢を選んだ場合は、続けて具体的な内容を AskUserQuestion で収集する:

- question: "<選択したカテゴリ>の具体的な内容を教えてください。"
- header: "Detail"
- multiSelect: false
- options（カテゴリに応じた代表的パターン）:

| カテゴリ | option 1 | option 2 | option 3 | option 4 |
|---------|----------|----------|----------|----------|
| ビルド・テスト系 | モジュール単位のビルド検証 | テスト実行と結果収集 | Lint/静的解析の実行 | CI パイプラインのローカル実行 |
| コード生成・変換系 | テンプレートからのコード生成 | API スキーマからのコード生成 | ファイルフォーマット変換 | 自動修正スクリプト |
| 環境セットアップ系 | 開発環境の初期構築 | worktree の管理 | 依存関係のインストール | シミュレータの管理 |
| 運用・メンテナンス系 | 不要ファイルのクリーンアップ | ログの収集・分析 | デプロイ・リリース補助 | バックアップ・リストア |

### ステップ 2: スクリプトの詳細情報を一括収集

AskUserQuestion で以下の4つの質問を同時に収集:

#### 質問 1: スクリプト名

- question: "スクリプトのファイル名を教えてください（拡張子 .sh は自動付与）。ケバブケースで指定してください（例: build-verify, snapshot-preview）。"
- header: "Filename"
- multiSelect: false
- options:
  1. **自動命名**: 目的から自動生成する

#### 質問 2: 入力（引数・環境変数）

- question: "スクリプトの入力を教えてください。位置引数（$1, $2...）と環境変数のどちらで受け取りますか？"
- header: "Input"
- multiSelect: false
- options:
  1. **引数なし**: 入力パラメータは不要
  2. **環境変数のみ**: 環境変数で設定を受け取る（デフォルト値あり）
  3. **位置引数のみ**: コマンドライン引数で受け取る
  4. **両方**: 環境変数と位置引数の両方を使用

#### 質問 3: 呼び出し元

- question: "このスクリプトを呼び出す Agent/Skill を教えてください。新規 Agent から呼び出す場合は Other で記述してください。"
- header: "Caller"
- multiSelect: false
- options:
  1. **手動実行のみ**: ユーザーが直接実行する
  2. **既存の Agent から呼び出し**: 既存の Agent から呼び出される
  3. **既存の Skill から呼び出し**: 既存の Skill から呼び出される
  4. **新規 Agent を作成予定**: これから作成する Agent から呼び出す

#### 質問 4: スクリプトの複雑度

- question: "スクリプトの複雑度を選択してください。これによりテンプレートの構造が変わります。"
- header: "Complexity"
- multiSelect: false
- options:
  1. **シンプル**: 単一処理、関数分割不要（例: check-diff.sh, test-verify.sh）
  2. **標準**: 複数ステップ、関数分割あり（例: build-verify.sh, snapshot-preview.sh）
  3. **複雑**: 並列処理、ロック機構、複数関数（例: simulator-run.sh, cleanup-claude-branches.sh）

### ステップ 3: 設計原則とベストプラクティスの取得

このステップでは2つの情報源を参照する。

#### 3.1 agent-friendly-cli ドキュメントの参照

スクリプトの特性に応じて `docs/agent-friendly-cli/` の該当ドキュメントを Read で読み込む:

| スクリプトの特性 | 参照ドキュメント |
|-----------------|----------------|
| 外部データの入出力がある | `docs/agent-friendly-cli/01-structured-io.md` |
| ユーザー入力やパスを受け取る | `docs/agent-friendly-cli/02-input-hardening.md` |
| Agent/Skill から呼び出される | `docs/agent-friendly-cli/03-skills-and-surfaces.md` |
| 破壊的操作（削除、変更）を含む | `docs/agent-friendly-cli/04-safety-rails.md` |

**全てのスクリプトに共通して適用すべき原則:**
- stdout はデータ出力のみ、進捗・ログは stderr へ（`>&2`）
- 終了コードで成功/失敗を明確に伝達
- エラーメッセージは構造的で、何が起きてどう対処すべきか伝わる内容にする

#### 3.2 ベストプラクティスの取得

**まずオフラインリファレンスを参照する。** [references/shell-best-practices-offline.md](references/shell-best-practices-offline.md) にエラーハンドリング、変数、パフォーマンス、スタイル、移植性、ShellCheck 警告の頻出パターンがバンドルされている。一般的なスクリプトではこのリファレンスで十分なことが多い。

**オフラインで不十分な場合のみ** `Task(shell-best-practices)` を呼び出し、Web 検索で補完する:

```text
以下のシェルスクリプトを実装するにあたり、ベストプラクティスを教えてください:

- 目的: {目的}
- 入力方式: {位置引数/環境変数/両方}
- 複雑度: {シンプル/標準/複雑}

特に以下の観点でアドバイスをお願いします:
1. エラーハンドリングの推奨パターン
2. パフォーマンスの考慮事項
3. 移植性（macOS/Linux 差異）の注意点
```

Task 呼び出しが必要なケース: 特殊な Bash 機能の仕様確認、個別の ShellCheck コード（SC2XXX）の詳細、オフラインリファレンスにないトピック。

### ステップ 4: テンプレート生成

収集した情報と選択された複雑度に基づき、スクリプトファイルを生成する。

#### 4.1 スクリプトファイルパス

```text
scripts/cli/{script-name}.sh
```

#### 4.2 テンプレート生成

複雑度別テンプレート、プレースホルダー置換ルール、ベストプラクティス反映方法は [references/templates.md](references/templates.md) を参照。

テンプレート生成時に、ステップ 3.1 で取得した agent-friendly-cli の原則を反映する:

| 原則 | テンプレートへの反映 |
|------|-------------------|
| stdout/stderr の分離 | ログ出力に `>&2` を使用。標準・複雑テンプレートでは `lib/common.sh` のログ関数が stderr に出力 |
| 入力バリデーション | パスを受け取る場合はパス走査チェックを追加 |
| 冪等性 | 可能な限り再実行しても安全な設計にする |
| dry-run | 破壊的操作を含む場合は `--dry-run` オプションを検討 |

#### 4.3 ファイル書き出しと権限設定

```bash
# スクリプトファイルを書き出し
Write scripts/cli/{script-name}.sh

# 実行権限を付与
chmod +x scripts/cli/{script-name}.sh
```

### ステップ 5: 品質検証

#### 5.1 shellcheck 実行

```bash
shellcheck scripts/cli/{script-name}.sh
```

shellcheck で警告やエラーが検出された場合:
- 自動修正可能なもの → Edit ツールで修正
- 修正にユーザー判断が必要なもの → 警告内容を提示して確認

#### 5.2 メタデータ完備チェック

以下のフィールドが全て存在するか確認:

- [ ] `@PURPOSE` が1行で簡潔に書かれているか
- [ ] `@INPUT` で全ての引数・環境変数が説明されているか
- [ ] `@OUTPUT` で終了コードの意味が明記されているか
- [ ] `@USAGE` で使用場面が明確か
- [ ] `@CALLER` で呼び出し元が正しく記載されているか

不足があれば自動補完する。

#### 5.3 agent-friendly 観点チェック

スクリプトの特性に応じて以下を確認:

- [ ] ログ・進捗メッセージが stderr に出力されているか（stdout を汚染していないか）
- [ ] パスを受け取る場合、パス走査の防御があるか
- [ ] 破壊的操作に確認ステップまたは dry-run オプションがあるか
- [ ] エラー時の終了コードが適切に設定されているか

### ステップ 6: 連携登録ガイド

スクリプトの呼び出し元に応じて、必要な登録手順を案内する。

#### 6.1 既存の Agent から呼び出す場合

```text
次のステップ:
1. Agent ファイル `.claude/agents/{agent-name}.md` の tools に Bash パターンを追加
   例: Bash(./scripts/cli/{script-name}.sh *)
2. Agent の実行手順にスクリプト呼び出しを追加
```

#### 6.2 新規 Agent を作成する場合

```text
次のステップ:
1. `.claude/agents/{new-agent-name}.md` を作成
2. Agent の tools に Bash パターンを追加
3. CLAUDE.md の Available Agents テーブルに追加
```

#### 6.3 手動実行の場合

```text
次のステップ:
1. 必要に応じて Makefile にターゲットを追加
2. CLAUDE.md に使用方法を記載（任意）
```

#### 6.4 CLAUDE.md 更新の必要性チェック

スクリプトが新規 Agent/Skill と連携する場合、CLAUDE.md の更新が必要であることを案内する。

CLAUDE.md や `.claude/` 配下のファイルを実際に更新した場合は、`Task(claude-instruction-reviewer)` を呼び出してベストプラクティスに沿っているか検証する。

### ステップ 7: 完了メッセージ

```markdown
## スクリプト作成完了

- **ファイル**: `scripts/cli/{script-name}.sh`
- **目的**: {目的}
- **呼び出し元**: {呼び出し元}
- **複雑度**: {シンプル|標準|複雑}
- **shellcheck**: {結果}

### 適用した設計原則

{agent-friendly-cli と shell-best-practices から適用した主要な原則のサマリー}

### 次のステップ

1. TODO コメントの箇所にメイン処理を実装
2. 実行テスト: `bash scripts/cli/{script-name}.sh`
3. {連携登録ガイドの内容}
4. コミット: `/commit`
```

## 命名規則

### スクリプト名

- **形式**: ケバブケース（小文字 + ハイフン）
- **拡張子**: `.sh`（自動付与）
- **配置場所**: `scripts/cli/`
- **良い例**: `build-verify`, `snapshot-preview`, `cleanup-claude-branches`
- **悪い例**: `buildVerify`（キャメルケース）, `build_verify`（スネークケース）

### メタデータフィールド

`.claude/rules/script-metadata.md` のフォーマットに従う。

## 共通ライブラリ

### `scripts/cli/lib/common.sh`

全スクリプトで共有するカラー定義・ログ関数・ユーティリティを提供する。

#### 利用方法

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
```

#### 提供する関数

| 関数 | 用途 | 出力先 |
|------|------|--------|
| `log_info` | 情報メッセージ | stderr |
| `log_success` | 成功メッセージ | stderr |
| `log_warn` | 警告メッセージ | stderr |
| `log_error` | エラーメッセージ | stderr |
| `log_step` | ステップ開始 | stderr |
| `log_detail` | 詳細情報 | stderr |
| `print_header` | ヘッダー表示 | stderr |
| `print_footer` | フッター表示 | stderr |
| `resolve_project_root` | プロジェクトルート解決 | stdout（パス文字列） |

#### 提供するカラー変数

| 変数 | 色 |
|------|-----|
| `BOLD` | 太字 |
| `DIM` | 淡色 |
| `RED` | 赤 |
| `GREEN` | 緑 |
| `YELLOW` | 黄 |
| `BLUE` | 青 |
| `CYAN` | シアン |
| `RESET` | リセット |

**注意**: シンプルテンプレートでは `lib/common.sh` を source しない（依存を最小化するため）。標準・複雑テンプレートでは必ず source する。

## エラー対応

### shellcheck で SC2086 (Double quote to prevent globbing) が出た場合

```bash
# Before
echo $VARIABLE
# After
echo "$VARIABLE"
```

### shellcheck で SC2155 (Declare and assign separately) が出た場合

```bash
# Before
local result=$(some_command)
# After
local result
result=$(some_command)
```

### メタデータフィールドが不足している場合

不足フィールドを自動補完し、ユーザーに確認する。

## 参照

- [references/templates.md](references/templates.md): 複雑度別テンプレート、プレースホルダー置換ルール、ベストプラクティス反映方法
- [references/shell-best-practices-offline.md](references/shell-best-practices-offline.md): シェルスクリプトの頻出ベストプラクティス（オフラインバンドル）
- `docs/agent-friendly-cli/README.md`: AI エージェント向け CLI 設計ガイド（目次）
- `docs/agent-friendly-cli/01-structured-io.md`: 構造化入出力
- `docs/agent-friendly-cli/02-input-hardening.md`: 入力バリデーション
- `docs/agent-friendly-cli/03-skills-and-surfaces.md`: スキルファイルとマルチサーフェス
- `docs/agent-friendly-cli/04-safety-rails.md`: 安全機構
- `docs/agent-friendly-cli/05-migration-checklist.md`: エージェント対応チェックリスト
- `.claude/rules/script-metadata.md`: メタデータヘッダーのフォーマット定義
- `.claude/agents/shell-best-practices.md`: 公式ドキュメント参照エージェント
- `.claude/agents/claude-instruction-reviewer.md`
- `scripts/cli/lib/common.sh`: 共通カラー・ログ・ユーティリティライブラリ
