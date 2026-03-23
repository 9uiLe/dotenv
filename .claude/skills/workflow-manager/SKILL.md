---
name: workflow-manager
description: YAML ベースのワークフロー定義から、タスクをサブタスクに分解し、worktree による分離実行と PR 自動作成を管理します。ワークフローの初期化、実行、状態確認、クリーンアップを行います。
allowed-tools: Read, Grep, Glob, Bash(./scripts/cli/workflow-cli.sh *)
argument-hint: "[yaml-or-task-id]"
---

# ワークフロー管理 (workflow-manager)

## 概要

YAML ベースのワークフロー定義に従い、タスクをステップに分解して worktree で分離実行するスキル。

## 使用方法

```text
/workflow-manager                     # 使い方を表示
/workflow-manager <yaml-file>         # ワークフロー YAML の describe + init
/workflow-manager status <task-id>    # 状態確認
```

## 実行フロー

### ケース 1: YAML ファイルが指定された場合

1. `workflow-cli.sh describe <yaml>` でバリデーションと内容確認
2. ユーザーに確認後、`workflow-cli.sh init <yaml>` でワークフロー初期化
3. `workflow-cli.sh status <task-id>` で状態表示

### ケース 2: task-id が指定された場合

1. `workflow-cli.sh status <task-id>` で現在の状態を確認
2. 次に実行すべきステップを提案

### ケース 3: 引数なし

ワークフローの概要と使い方を表示。

## CLI コマンドリファレンス

| コマンド | 説明 |
|---------|------|
| `workflow-cli.sh describe <yaml>` | YAML のバリデーションと内容表示 |
| `workflow-cli.sh init <yaml>` | ワークフロー初期化 |
| `workflow-cli.sh run <task-id>` | 全 pending ステップを実行 |
| `workflow-cli.sh run-step <task-id> <step>` | 特定ステップを実行/完了 |
| `workflow-cli.sh status <task-id>` | 状態表示 |
| `workflow-cli.sh list` | 全ワークフロー一覧 |
| `workflow-cli.sh add-step <task-id> <name>` | ステップ追加 |
| `workflow-cli.sh skip-step <task-id> <step>` | ステップスキップ |
| `workflow-cli.sh cleanup <task-id>` | クリーンアップ |
| `workflow-cli.sh cleanup-all` | 全クリーンアップ |

## ワークフロー YAML 形式

```yaml
name: workflow-name
description: "ワークフローの説明"

branch:
  base: master
  prefix: workflow

settings:
  auto-pr: true
  pr-labels:
    - enhancement
  pr-assignee: "@me"

steps:
  - name: step-name
    description: "ステップの説明"
    prompt: |
      ステップで実行する内容の指示...
    depends-on: []
```

## テンプレート

テンプレートは `.workflow/templates/` に配置:

- `basic.yaml`: 基本テンプレート（3 ステップ構成）

## 注意事項

- `yq` が必要（`brew install yq` でインストール）
- worktree は `.worktrees/` に作成される（`.gitignore` 済み）
- 状態ファイルは `.workflow/state/` に保存される（`.gitignore` 済み）
