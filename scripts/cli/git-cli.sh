#!/bin/bash
#
# @PURPOSE: git 操作の CLI。許可されたサブコマンドのみを提供し、引数のパススルーを禁止する
# @INPUT:
#   - $1: サブコマンド (status, staged-files, diff, commit, push, etc.)
#   - $2...: サブコマンド固有のパラメータ（ファイルパス、メッセージ、ブランチ名）
# @OUTPUT:
#   - 終了コード 0: git コマンドの正常終了
#   - 終了コード 1: 不正なサブコマンドまたはパラメータ
#   - 終了コード 非0: git コマンドのエラー
# @USAGE: git コマンドを直接実行する代わりに使用する
# @CALLER: script-creator Skill, 手動実行
# @EXAMPLE:
#   ./scripts/cli/git-cli.sh status
#   ./scripts/cli/git-cli.sh staged-files
#   ./scripts/cli/git-cli.sh commit "feat: add feature"
#   ./scripts/cli/git-cli.sh push
#

source "$(dirname "$0")/lib/common.sh"
init_error_handling

# --- ヘルパー関数 ---

# 位置引数にフラグ（- で始まる引数）が含まれていないか検証
reject_flags() {
    for arg in "$@"; do
        case "$arg" in
            -*)
                log_error "Flags are not allowed: $arg"
                log_error "Only positional arguments (file paths, branch names) are accepted."
                exit 1
                ;;
        esac
    done
}

show_usage() {
    cat >&2 <<'USAGE'
Usage: git-cli.sh <subcommand> [params...]

Read-only subcommands:
  status                        git status --short
  staged-files                  git diff --cached --name-only
  changed-files                 git diff --name-only
  diff-names [base]             git diff ${base:-origin/master}...HEAD --name-only
  diff-stat [base]              git diff ${base:-origin/master}...HEAD --stat
  diff [paths...]               git diff -- [paths...]
  diff-staged [paths...]        git diff --cached -- [paths...] (alias: diff-cached)
  log [base]                    git log ${base:-origin/master}..HEAD --oneline
  branch-name                   git rev-parse --abbrev-ref HEAD
  repo-root                     git rev-parse --show-toplevel
  branch-list                   git branch

Write subcommands:
  add <paths...>                git add -- <paths...>
  commit <message>              git commit -m <message>
  commit-amend <message>        git commit --amend -m <message>
  push                          git push origin HEAD
  push-upstream                 git push -u origin HEAD
  pull-rebase <branch>          git pull --rebase origin <branch>
  checkout <branch>             git checkout <branch>
  checkout-new <branch> [start] git checkout -b <branch> [start] (start: master, workflow/*)
  fetch-master                  git fetch origin master --quiet
  merge <branch>                git merge origin/<branch> --no-edit (master, workflow/*)
  merge-continue                git commit --no-edit (merge continuation)
  merge-abort                   git merge --abort

Worktree subcommands:
  worktree-add <path> <branch>                  git worktree add <path> <branch>
  worktree-add-new <path> <branch> <start>      git worktree add -b <branch> <path> <start>
  worktree-remove <path>                        git worktree remove <path> --force
  worktree-list                                 git worktree list
  worktree-prune                                git worktree prune
USAGE
}

# --- 引数チェック ---
if [ $# -eq 0 ]; then
    log_error "No subcommand specified."
    show_usage
    exit 1
fi

SUBCOMMAND="$1"
shift

# --- サブコマンドのディスパッチ ---
case "$SUBCOMMAND" in

    # =====================================================================
    # 読み取り専用サブコマンド
    # =====================================================================

    status)
        [ $# -ne 0 ] && { log_error "status takes no arguments."; exit 1; }
        exec git status --short
        ;;

    staged-files)
        [ $# -ne 0 ] && { log_error "staged-files takes no arguments."; exit 1; }
        exec git diff --cached --name-only
        ;;

    changed-files)
        [ $# -ne 0 ] && { log_error "changed-files takes no arguments."; exit 1; }
        exec git diff --name-only
        ;;

    diff-names)
        reject_flags "$@"
        local_base="${1:-origin/master}"
        exec git diff "${local_base}...HEAD" --name-only
        ;;

    diff-stat)
        reject_flags "$@"
        local_base="${1:-origin/master}"
        exec git diff "${local_base}...HEAD" --stat
        ;;

    diff)
        reject_flags "$@"
        if [ $# -eq 0 ]; then
            exec git diff
        else
            exec git diff -- "$@"
        fi
        ;;

    diff-staged|diff-cached)
        reject_flags "$@"
        if [ $# -eq 0 ]; then
            exec git diff --cached
        else
            exec git diff --cached -- "$@"
        fi
        ;;

    log)
        reject_flags "$@"
        local_base="${1:-origin/master}"
        exec git log "${local_base}..HEAD" --oneline
        ;;

    branch-name)
        [ $# -ne 0 ] && { log_error "branch-name takes no arguments."; exit 1; }
        exec git rev-parse --abbrev-ref HEAD
        ;;

    repo-root)
        [ $# -ne 0 ] && { log_error "repo-root takes no arguments."; exit 1; }
        exec git rev-parse --show-toplevel
        ;;

    branch-list)
        [ $# -ne 0 ] && { log_error "branch-list takes no arguments."; exit 1; }
        exec git branch
        ;;

    # =====================================================================
    # 書き込みサブコマンド（厳密なパラメータ制御）
    # =====================================================================

    add)
        [ $# -eq 0 ] && { log_error "add requires at least one path."; exit 1; }
        reject_flags "$@"
        exec git add -- "$@"
        ;;

    commit)
        [ $# -ne 1 ] && { log_error "commit requires exactly one argument: <message>"; exit 1; }
        exec git commit -m "$1"
        ;;

    commit-amend)
        [ $# -ne 1 ] && { log_error "commit-amend requires exactly one argument: <message>"; exit 1; }
        exec git commit --amend -m "$1"
        ;;

    push)
        [ $# -ne 0 ] && { log_error "push takes no arguments. Always pushes to origin HEAD."; exit 1; }
        exec git push origin HEAD
        ;;

    push-upstream)
        [ $# -ne 0 ] && { log_error "push-upstream takes no arguments. Always pushes to origin HEAD with -u."; exit 1; }
        exec git push -u origin HEAD
        ;;

    pull-rebase)
        [ $# -ne 1 ] && { log_error "pull-rebase requires exactly one argument: <branch>"; exit 1; }
        reject_flags "$@"
        exec git pull --rebase origin "$1"
        ;;

    checkout)
        [ $# -ne 1 ] && { log_error "checkout requires exactly one argument: <branch>"; exit 1; }
        reject_flags "$@"
        exec git checkout "$1"
        ;;

    checkout-new)
        [ $# -lt 1 ] || [ $# -gt 2 ] && { log_error "checkout-new requires 1-2 arguments: <branch> [start-point]"; exit 1; }
        reject_flags "$@"
        if [ $# -eq 2 ]; then
            case "$2" in
                master|workflow/*)
                    ;;
                *)
                    log_error "start-point is restricted to: master, workflow/*"
                    exit 1
                    ;;
            esac
            exec git checkout -b "$1" "$2"
        else
            exec git checkout -b "$1"
        fi
        ;;

    fetch-master)
        [ $# -ne 0 ] && { log_error "fetch-master takes no arguments."; exit 1; }
        exec git fetch origin master --quiet
        ;;

    merge)
        [ $# -ne 1 ] && { log_error "merge requires exactly one argument: <branch>"; exit 1; }
        reject_flags "$@"
        case "$1" in
            master|workflow/*)
                ;;
            *)
                log_error "merge is restricted to base branches only: master, workflow/*"
                log_error "To merge other branches, use GitHub pull requests."
                exit 1
                ;;
        esac
        exec git merge "origin/$1" --no-edit
        ;;

    merge-continue)
        [ $# -ne 0 ] && { log_error "merge-continue takes no arguments."; exit 1; }
        exec git commit --no-edit
        ;;

    merge-abort)
        [ $# -ne 0 ] && { log_error "merge-abort takes no arguments."; exit 1; }
        exec git merge --abort
        ;;

    # =====================================================================
    # Worktree サブコマンド
    # =====================================================================

    worktree-add)
        [ $# -ne 2 ] && { log_error "worktree-add requires exactly two arguments: <path> <branch>"; exit 1; }
        reject_flags "$@"
        local_path="$1"
        local_branch="$2"
        case "$local_path" in
            .worktrees/*) ;;
            *) log_error "worktree path must be under .worktrees/"; exit 1 ;;
        esac
        exec git worktree add "$local_path" "$local_branch"
        ;;

    worktree-add-new)
        [ $# -ne 3 ] && { log_error "worktree-add-new requires exactly three arguments: <path> <branch> <start-point>"; exit 1; }
        reject_flags "$@"
        local_path="$1"
        local_branch="$2"
        local_start="$3"
        case "$local_path" in
            .worktrees/*) ;;
            *) log_error "worktree path must be under .worktrees/"; exit 1 ;;
        esac
        case "$local_start" in
            master|origin/master|workflow/*|origin/workflow/*) ;;
            *) log_error "start-point is restricted to: master, origin/master, workflow/*, origin/workflow/*"; exit 1 ;;
        esac
        exec git worktree add -b "$local_branch" "$local_path" "$local_start"
        ;;

    worktree-remove)
        [ $# -ne 1 ] && { log_error "worktree-remove requires exactly one argument: <path>"; exit 1; }
        reject_flags "$@"
        local_path="$1"
        case "$local_path" in
            .worktrees/*) ;;
            *) log_error "worktree path must be under .worktrees/"; exit 1 ;;
        esac
        exec git worktree remove "$local_path" --force
        ;;

    worktree-list)
        [ $# -ne 0 ] && { log_error "worktree-list takes no arguments."; exit 1; }
        exec git worktree list
        ;;

    worktree-prune)
        [ $# -ne 0 ] && { log_error "worktree-prune takes no arguments."; exit 1; }
        exec git worktree prune
        ;;

    # =====================================================================
    # 未知のサブコマンド
    # =====================================================================

    *)
        log_error "Unknown subcommand: $SUBCOMMAND"
        show_usage
        exit 1
        ;;
esac
