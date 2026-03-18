---
paths: "**"
---

# git/gh CLI 使用ルール

## 概要

`git` および `gh` コマンドを直接実行してはならない。必ず専用 CLI スクリプト経由で実行すること。

## ルール

### git コマンド

`git` コマンドは `./scripts/cli/git-cli.sh` の専用サブコマンドで実行する。

```bash
# NG: 直接実行
git status
git diff --cached

# OK: CLI 経由
./scripts/cli/git-cli.sh status
./scripts/cli/git-cli.sh staged-files
./scripts/cli/git-cli.sh commit "feat: add feature"
./scripts/cli/git-cli.sh push
./scripts/cli/git-cli.sh merge master
```

### gh コマンド

`gh` コマンドは `./scripts/cli/gh-cli.sh` の専用サブコマンドで実行する。

```bash
# NG: 直接実行
gh pr list
gh issue view 123

# OK: CLI 経由
./scripts/cli/gh-cli.sh pr-list
./scripts/cli/gh-cli.sh issue-view 123
./scripts/cli/gh-cli.sh pr-create --base master --title "feat: add" --label "enhancement" --assignee "@me" --body "body"
```

## 設計方針

- 各サブコマンドは内部で git/gh コマンドを構築する（引数のパススルー禁止）
- 必要なパラメータ（ファイルパス、メッセージ、ブランチ名）のみ受け取る
- フラグ（`-` で始まる引数）は位置引数として受け付けない
- 未知のオプションは拒否する

## 理由

専用 CLI により、許可された操作のみを明示的に提供し、危険な操作（force push、設定変更、リポジトリ削除等）の実行を構造的に不可能にする。
