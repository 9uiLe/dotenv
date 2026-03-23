# ワークフロー CLI ルール

## 概要

`workflow-cli.sh` を使用してワークフローベースのタスク分解・実行を管理する。

## ルール

### workflow-cli コマンド

ワークフロー操作は `./scripts/cli/workflow-cli.sh` のサブコマンドで実行する。

```bash
# ワークフロー YAML のバリデーション・確認
./scripts/cli/workflow-cli.sh describe .workflow/templates/basic.yaml

# ワークフロー初期化
./scripts/cli/workflow-cli.sh init .workflow/templates/basic.yaml

# ステップ実行（pending → running にして worktree セットアップ）
./scripts/cli/workflow-cli.sh run-step <task-id> <step-name>

# ステップ完了（running → push + PR → completed）
./scripts/cli/workflow-cli.sh run-step <task-id> <step-name>

# 状態確認
./scripts/cli/workflow-cli.sh status <task-id>

# 一覧
./scripts/cli/workflow-cli.sh list

# クリーンアップ
./scripts/cli/workflow-cli.sh cleanup <task-id>
```

## ステップ実行の流れ

1. `run-step` を初回実行 → worktree を作成し、プロンプトを表示。ステップは `running` になる
2. worktree 内で作業・コミット
3. `run-step` を再度実行 → push + PR 作成。ステップは `completed` になる

## ブランチ命名規則

```
workflow/{timestamp}-{slug}                          (ベースブランチ)
workflow/{timestamp}-{slug}/step-01-{step-name}      (ステップブランチ)
```

## 前提条件

- `yq` コマンドが必要（`brew install yq`）
- ワークフロー YAML は `.workflow/templates/` に配置
