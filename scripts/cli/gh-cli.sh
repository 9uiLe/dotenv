#!/bin/bash
#
# @PURPOSE: gh 操作の CLI。許可されたサブコマンドのみを提供し、危険な操作（merge, delete 等）を排除する
# @INPUT:
#   - $1: サブコマンド (pr-view, pr-list, pr-create, issue-view, etc.)
#   - $2...: サブコマンド固有のパラメータ（名前付きオプション）
# @OUTPUT:
#   - 終了コード 0: gh コマンドの正常終了
#   - 終了コード 1: 不正なサブコマンドまたはパラメータ
#   - 終了コード 非0: gh コマンドのエラー
# @USAGE: gh コマンドを直接実行する代わりに使用する
# @CALLER: script-creator Skill, 手動実行
# @EXAMPLE:
#   ./scripts/cli/gh-cli.sh pr-view --json url,title
#   ./scripts/cli/gh-cli.sh pr-create --base master --title "feat: add" --label "enhancement" --assignee "@me" --body "body"
#   ./scripts/cli/gh-cli.sh issue-view 123
#

source "$(dirname "$0")/lib/common.sh"
init_error_handling

show_usage() {
    cat >&2 <<'USAGE'
Usage: gh-cli.sh <subcommand> [params...]

PR subcommands:
  pr-view [--json fields] [--jq expr]
  pr-list [--head branch] [--state state] [--json fields] [--jq expr]
  pr-diff-files
  pr-comment-reply <owner> <repo> <pull_number> <comment_id> --body <body>
  pr-create --base <base> --title <title> --label <label>... --assignee <user> --body <body>

Issue subcommands:
  issue-view <url-or-number>
  issue-create --title <title> --body <body> [--label <label>...] [--assignee <user>]
  issue-comment <number> --body <body>

Label subcommands:
  label-list
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
    # PR サブコマンド
    # =====================================================================

    pr-view)
        # 許可オプション: --json, --jq
        args=()
        while [ $# -gt 0 ]; do
            case "$1" in
                --json) [ -z "${2:-}" ] && { log_error "pr-view: --json requires a value."; exit 1; }; args+=(--json "$2"); shift 2 ;;
                --jq)   [ -z "${2:-}" ] && { log_error "pr-view: --jq requires a value."; exit 1; };   args+=(--jq "$2");   shift 2 ;;
                *)      log_error "pr-view: unknown option: $1"; exit 1 ;;
            esac
        done
        exec gh pr view "${args[@]}"
        ;;

    pr-list)
        # 許可オプション: --head, --state, --json, --jq
        args=()
        while [ $# -gt 0 ]; do
            case "$1" in
                --head)  [ -z "${2:-}" ] && { log_error "pr-list: --head requires a value."; exit 1; };  args+=(--head "$2");  shift 2 ;;
                --state) [ -z "${2:-}" ] && { log_error "pr-list: --state requires a value."; exit 1; }; args+=(--state "$2"); shift 2 ;;
                --json)  [ -z "${2:-}" ] && { log_error "pr-list: --json requires a value."; exit 1; };  args+=(--json "$2");  shift 2 ;;
                --jq)    [ -z "${2:-}" ] && { log_error "pr-list: --jq requires a value."; exit 1; };    args+=(--jq "$2");    shift 2 ;;
                *)       log_error "pr-list: unknown option: $1"; exit 1 ;;
            esac
        done
        exec gh pr list "${args[@]}"
        ;;

    pr-diff-files)
        [ $# -ne 0 ] && { log_error "pr-diff-files takes no arguments."; exit 1; }
        exec gh pr diff --name-only
        ;;

    pr-comment-reply)
        # PR レビューコメントへの返信
        # $1: owner, $2: repo, $3: pull_number, $4: comment_id, --body: 必須
        [ $# -lt 4 ] && { log_error "pr-comment-reply requires: <owner> <repo> <pull_number> <comment_id> --body <body>"; exit 1; }
        local_owner="$1"; local_repo="$2"; local_pull_number="$3"; local_comment_id="$4"
        shift 4
        local_body=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --body) [ -z "${2:-}" ] && { log_error "pr-comment-reply: --body requires a value."; exit 1; }; local_body="$2"; shift 2 ;;
                *)      log_error "pr-comment-reply: unknown option: $1"; exit 1 ;;
            esac
        done
        [ -z "$local_body" ] && { log_error "pr-comment-reply: --body is required."; exit 1; }
        exec gh api "repos/${local_owner}/${local_repo}/pulls/${local_pull_number}/comments/${local_comment_id}/replies" -f body="$local_body"
        ;;

    pr-create)
        # 必須: --base, --title, --body, --label (1つ以上), --assignee
        local_base="" local_title="" local_body=""
        labels=()
        local_assignee=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --base)     [ -z "${2:-}" ] && { log_error "pr-create: --base requires a value."; exit 1; };     local_base="$2";     shift 2 ;;
                --title)    [ -z "${2:-}" ] && { log_error "pr-create: --title requires a value."; exit 1; };    local_title="$2";    shift 2 ;;
                --body)     [ -z "${2:-}" ] && { log_error "pr-create: --body requires a value."; exit 1; };     local_body="$2";     shift 2 ;;
                --label)    [ -z "${2:-}" ] && { log_error "pr-create: --label requires a value."; exit 1; };    labels+=("$2");      shift 2 ;;
                --assignee) [ -z "${2:-}" ] && { log_error "pr-create: --assignee requires a value."; exit 1; }; local_assignee="$2"; shift 2 ;;
                *)          log_error "pr-create: unknown option: $1"; exit 1 ;;
            esac
        done
        [ -z "$local_base" ]     && { log_error "pr-create: --base is required."; exit 1; }
        [ -z "$local_title" ]    && { log_error "pr-create: --title is required."; exit 1; }
        [ -z "$local_body" ]     && { log_error "pr-create: --body is required."; exit 1; }
        [ ${#labels[@]} -eq 0 ]  && { log_error "pr-create: --label is required (at least one)."; exit 1; }
        [ -z "$local_assignee" ] && { log_error "pr-create: --assignee is required."; exit 1; }

        # Base branch validation: master or workflow/* branches only
        case "$local_base" in
            master|workflow/*)
                ;;
            *)
                log_error "pr-create: --base is restricted to: master, workflow/*"
                exit 1
                ;;
        esac

        # ラベルのバリデーション: リポジトリの実在ラベルと照合
        valid_labels=$(gh label list --limit 100 --json name --jq '.[].name')
        for label in "${labels[@]}"; do
            if ! echo "$valid_labels" | grep -qxF -- "$label"; then
                log_error "pr-create: unknown label: '${label}'"
                log_error "Valid labels:"
                echo "$valid_labels" | sort | while IFS= read -r l; do
                    echo "  - $l" >&2
                done
                exit 1
            fi
        done

        args=(--base "$local_base" --title "$local_title" --body "$local_body" --assignee "$local_assignee")
        for label in "${labels[@]}"; do
            args+=(--label "$label")
        done
        exec gh pr create "${args[@]}"
        ;;

    # =====================================================================
    # Issue サブコマンド
    # =====================================================================

    issue-view)
        [ $# -ne 1 ] && { log_error "issue-view requires exactly one argument: <url-or-number>"; exit 1; }
        case "$1" in
            -*) log_error "issue-view: flags are not allowed. Provide a URL or issue number."; exit 1 ;;
        esac
        exec gh issue view "$1"
        ;;

    issue-create)
        # 必須: --title, --body
        # 任意: --label (複数可), --assignee
        local_title="" local_body=""
        labels=()
        local_assignee=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --title)    [ -z "${2:-}" ] && { log_error "issue-create: --title requires a value."; exit 1; };    local_title="$2";    shift 2 ;;
                --body)     [ -z "${2:-}" ] && { log_error "issue-create: --body requires a value."; exit 1; };     local_body="$2";     shift 2 ;;
                --label)    [ -z "${2:-}" ] && { log_error "issue-create: --label requires a value."; exit 1; };    labels+=("$2");      shift 2 ;;
                --assignee) [ -z "${2:-}" ] && { log_error "issue-create: --assignee requires a value."; exit 1; }; local_assignee="$2"; shift 2 ;;
                *)          log_error "issue-create: unknown option: $1"; exit 1 ;;
            esac
        done
        [ -z "$local_title" ] && { log_error "issue-create: --title is required."; exit 1; }
        [ -z "$local_body" ]  && { log_error "issue-create: --body is required."; exit 1; }

        args=(--title "$local_title" --body "$local_body")
        for label in "${labels[@]}"; do
            args+=(--label "$label")
        done
        [ -n "$local_assignee" ] && args+=(--assignee "$local_assignee")
        exec gh issue create "${args[@]}"
        ;;

    issue-comment)
        # $1: number, --body: 必須
        [ $# -lt 1 ] && { log_error "issue-comment requires: <number> --body <body>"; exit 1; }
        local_number="$1"
        shift
        case "$local_number" in
            -*) log_error "issue-comment: first argument must be an issue number, not a flag."; exit 1 ;;
        esac

        local_body=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --body) [ -z "${2:-}" ] && { log_error "issue-comment: --body requires a value."; exit 1; }; local_body="$2"; shift 2 ;;
                *)      log_error "issue-comment: unknown option: $1"; exit 1 ;;
            esac
        done
        [ -z "$local_body" ] && { log_error "issue-comment: --body is required."; exit 1; }

        exec gh issue comment "$local_number" --body "$local_body"
        ;;

    # =====================================================================
    # Label サブコマンド
    # =====================================================================

    label-list)
        [ $# -ne 0 ] && { log_error "label-list takes no arguments."; exit 1; }
        exec gh label list --limit 100
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
