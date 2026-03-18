# スキル構造

[← 目次に戻る](./README.md)

---

## フロントマター

### 必須フィールド

```yaml
---
name: skill-name
description: スキルの説明文（三人称、トリガーワード含む）
allowed-tools: Tool1, Tool2, Task(agent-name)
---
```

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | 必須 | ディレクトリ名と一致すること。小文字・数字・ハイフンのみ |
| `description` | 必須 | スキルの説明。三人称で記述。最大1024文字 |
| `allowed-tools` | 必須 | スキルが使用するツール一覧 |

### 任意フィールド

| フィールド | 説明 | 使用例 |
|-----------|------|--------|
| `argument-hint` | 引数のヒント表示 | `[要件説明]` |

### フィールド順序

プロジェクトの既存パターンに従い、以下の順序で記述する:

1. `name`
2. `description`
3. `argument-hint`（使用する場合）
4. `allowed-tools`

### allowed-tools の書き方

```yaml
# 基本ツール
allowed-tools: Read, Grep, Glob, Write, Edit

# Bash（パターン指定）
allowed-tools: Bash(./scripts/cli/git-cli.sh *), Bash(ls *), Bash(shellcheck *)

# SubAgent 呼び出し
allowed-tools: Task(agent-name)

# 他のスキル呼び出し
allowed-tools: Skill(skill-name)

# ユーザーへの質問
allowed-tools: AskUserQuestion

# 複合例
allowed-tools: Read, Grep, Glob, Bash(./scripts/cli/git-cli.sh *), Task(shell-best-practices), AskUserQuestion
```

---

## セクション構成

### 推奨構成

```markdown
# スキル名 (skill-name)

## 概要
スキルの目的と提供する機能の概要。

## 使用方法
呼び出し方法と引数の説明。

## 実行フロー
ステップバイステップの実行フロー。

## 命名規則（必要な場合）
ファイル名や変数名の規則。

## エラー対応（必要な場合）
よくあるエラーとその対処法。

## 参照
関連する Rules、Agents、ドキュメントへのリンク。
```

### セクション別の説明

#### 概要

- スキルの目的を1-2文で記述
- 提供する機能を箇条書きで列挙

```markdown
## 概要

`scripts/cli/` 配下のシェルスクリプトを、プロジェクトのベストプラクティスに基づいてガイド付きで作成する Skill。以下の機能を提供:

1. **対話的な要件収集**: スクリプトの目的、入出力、呼び出し元を対話的に収集
2. **ベストプラクティス参照**: `Task(shell-best-practices)` で公式ドキュメントに基づく最適な実装方法を取得
3. **テンプレート生成**: メタデータヘッダー、エラーハンドリング、共通ライブラリを利用したスクリプトを生成
```

#### 使用方法

- 基本使用（引数なし）と引数付きの両方を記載

```markdown
## 使用方法

### 基本使用

/skill-name

### 引数付き

/skill-name <引数の説明>
```

#### 実行フロー

- **ステップ番号** を付ける（例: ステップ 1, ステップ 2）
- 各ステップの **入力と出力** を明確にする
- AskUserQuestion を使用する場合は、質問内容・選択肢を具体的に記載

#### 参照

- 関連する Agent、Rule、ドキュメントの相対パスを列挙

```markdown
## 参照

- `.claude/rules/script-metadata.md`
- `.claude/agents/shell-best-practices.md`
- `scripts/cli/lib/common.sh`
```

---

## ディレクトリ構成

### シンプル型（SKILL.md のみ）

500行以下で完結するスキル。

```text
.claude/skills/my-skill/
└── SKILL.md
```

**適用例**: 参照ファイルが不要なシンプルなスキル

### 複合型（SKILL.md + reference.md）

SKILL.md が大きくなる場合、詳細テンプレートを reference.md に分離。

```text
.claude/skills/example-skill/
├── SKILL.md          # 概要 + 実行フロー
├── reference.md      # Agent 呼び出しテンプレート、出力フォーマット
└── examples.md       # 自律実行フロー、エラー対応（任意）
```

**適用例**: 参照ファイルが多い場合に適用

### 分離の判断基準

| 条件 | 推奨 |
|------|------|
| SKILL.md が 300行以下 | シンプル型で十分 |
| SKILL.md が 300-500行 | 詳細テンプレートの分離を検討 |
| SKILL.md が 500行超 | 複合型に分離（必須） |

---

## 命名規則

### スキル名（ディレクトリ名）

- **形式**: 小文字 + ハイフン区切り
- **動詞で始めることを推奨**: create-, update-, manage-, diff-
- **例**: `script-creator`, `diff-review`, `manage-resources`, `update-openapi-client`

### ファイル名

| ファイル | 固定名 |
|---------|--------|
| メイン | `SKILL.md`（大文字、固定） |
| 詳細参照 | `reference.md` |
| 実行例 | `examples.md` |
