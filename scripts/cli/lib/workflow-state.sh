#!/bin/bash
#
# @PURPOSE: ワークフロー実行状態の管理（状態ファイルの CRUD）
# @INPUT: なし（source で読み込む）
# @OUTPUT: 状態管理関数をエクスポート
# @USAGE: source "$(dirname "$0")/lib/workflow-state.sh"
# @CALLER: workflow-cli.sh, workflow-engine.sh
#

# --- 多重読み込み防止 ---
if [ -n "${_WORKFLOW_STATE_SH_LOADED:-}" ]; then
    return 0
fi
_WORKFLOW_STATE_SH_LOADED=1

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# --- 定数 ---

_WORKFLOW_STATE_DIR=".workflow/state"

# --- ディレクトリ管理 ---

# 状態ディレクトリを確保
workflow_state_ensure_dir() {
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    mkdir -p "${repo_root}/${_WORKFLOW_STATE_DIR}"
}

# 状態ファイルのパスを返す
workflow_state_file() {
    local task_id="$1"
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    echo "${repo_root}/${_WORKFLOW_STATE_DIR}/${task_id}.yaml"
}

# --- 状態ファイルの CRUD ---

# 状態ファイルが存在するか確認
workflow_state_exists() {
    local task_id="$1"
    local state_file
    state_file=$(workflow_state_file "$task_id")
    [ -f "$state_file" ]
}

# 初期状態ファイルを作成
# 引数: $1=task_id, $2=workflow_name, $3=source_yaml, $4=base_branch, $5=step_names (改行区切り)
workflow_state_create() {
    local task_id="$1"
    local workflow_name="$2"
    local source_yaml="$3"
    local base_branch="$4"
    local step_names="$5"

    require_command "yq" "brew install yq"
    workflow_state_ensure_dir

    local state_file
    state_file=$(workflow_state_file "$task_id")

    local created_at
    created_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # 初期状態 YAML を作成
    cat > "$state_file" <<EOF
task-id: "$task_id"
workflow-name: "$workflow_name"
source-yaml: "$source_yaml"
base-branch: "$base_branch"
created-at: "$created_at"
status: "pending"
steps: []
EOF

    # ステップを追加
    local index=1
    while IFS= read -r step_name; do
        [ -z "$step_name" ] && continue
        local step_num
        step_num=$(printf "%02d" "$index")
        local branch_prefix="${base_branch%/base}"
        local step_branch="${branch_prefix}/step-${step_num}-${step_name}"
        local step_worktree=".worktrees/${branch_prefix}/step-${step_num}-${step_name}"

        yq -i ".steps += [{
            \"name\": \"$step_name\",
            \"index\": $index,
            \"branch\": \"$step_branch\",
            \"worktree\": \"$step_worktree\",
            \"status\": \"pending\",
            \"pr-url\": \"\"
        }]" "$state_file"

        index=$((index + 1))
    done <<< "$step_names"

    echo "$state_file"
}

# 状態ファイルの汎用ゲッター
workflow_state_get() {
    local task_id="$1"
    local yq_expr="$2"
    local state_file
    state_file=$(workflow_state_file "$task_id")
    if [ ! -f "$state_file" ]; then
        log_error "State file not found for task: $task_id"
        return 1
    fi
    yq "$yq_expr" "$state_file" 2>/dev/null
}

# 状態ファイルの汎用セッター
workflow_state_set() {
    local task_id="$1"
    local yq_expr="$2"
    local state_file
    state_file=$(workflow_state_file "$task_id")
    if [ ! -f "$state_file" ]; then
        log_error "State file not found for task: $task_id"
        return 1
    fi
    yq -i "$yq_expr" "$state_file"
}

# --- ワークフローレベル ---

# ワークフロー全体のステータスを取得
workflow_state_get_status() {
    workflow_state_get "$1" '.status'
}

# ワークフロー全体のステータスを設定
workflow_state_set_status() {
    local task_id="$1"
    local status="$2"
    workflow_state_set "$task_id" ".status = \"$status\""
}

# ベースブランチ名を取得
workflow_state_get_base_branch() {
    workflow_state_get "$1" '.base-branch'
}

# ソース YAML パスを取得
workflow_state_get_source_yaml() {
    workflow_state_get "$1" '.source-yaml'
}

# --- ステップレベル ---

# ステップ数を取得
workflow_state_get_step_count() {
    workflow_state_get "$1" '.steps | length'
}

# ステップ名一覧を取得
workflow_state_get_step_names() {
    workflow_state_get "$1" '.steps[].name'
}

# ステップのインデックスを名前から検索（0ベース）
workflow_state_find_step_index() {
    local task_id="$1"
    local step_name="$2"
    local state_file
    state_file=$(workflow_state_file "$task_id")
    yq ".steps | to_entries | .[] | select(.value.name == \"$step_name\") | .key" "$state_file" 2>/dev/null
}

# ステップのステータスを取得
workflow_state_get_step_status() {
    local task_id="$1"
    local step_name="$2"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_get "$task_id" ".steps[$index].status"
}

# ステップのステータスを設定
workflow_state_set_step_status() {
    local task_id="$1"
    local step_name="$2"
    local status="$3"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_set "$task_id" ".steps[$index].status = \"$status\""
}

# ステップのブランチ名を取得
workflow_state_get_step_branch() {
    local task_id="$1"
    local step_name="$2"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_get "$task_id" ".steps[$index].branch"
}

# ステップの worktree パスを取得
workflow_state_get_step_worktree() {
    local task_id="$1"
    local step_name="$2"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_get "$task_id" ".steps[$index].worktree"
}

# ステップの PR URL を設定
workflow_state_set_step_pr_url() {
    local task_id="$1"
    local step_name="$2"
    local pr_url="$3"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_set "$task_id" ".steps[$index].pr-url = \"$pr_url\""
}

# ステップの PR URL を取得
workflow_state_get_step_pr_url() {
    local task_id="$1"
    local step_name="$2"
    local index
    index=$(workflow_state_find_step_index "$task_id" "$step_name")
    if [ -z "$index" ]; then
        log_error "Step not found: $step_name"
        return 1
    fi
    workflow_state_get "$task_id" ".steps[$index].pr-url"
}

# --- 一覧・表示 ---

# 全ワークフローの一覧を表示
workflow_state_list() {
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    local state_dir="${repo_root}/${_WORKFLOW_STATE_DIR}"

    if [ ! -d "$state_dir" ]; then
        log_info "No workflows found."
        return 0
    fi

    local count=0
    for state_file in "$state_dir"/*.yaml; do
        [ -f "$state_file" ] || continue
        local task_id wf_name status created_at
        task_id=$(yq '.task-id' "$state_file" 2>/dev/null)
        wf_name=$(yq '.workflow-name' "$state_file" 2>/dev/null)
        status=$(yq '.status' "$state_file" 2>/dev/null)
        created_at=$(yq '.created-at' "$state_file" 2>/dev/null)

        local total_steps completed_steps
        total_steps=$(yq '.steps | length' "$state_file" 2>/dev/null)
        completed_steps=$(yq '.steps[] | select(.status == "completed") | .name' "$state_file" 2>/dev/null | wc -l | tr -d ' ')

        echo -e "${CYAN}${task_id}${RESET}  ${wf_name}  [${status}]  ${completed_steps}/${total_steps} steps  (${created_at})" >&2
        count=$((count + 1))
    done

    if [ "$count" -eq 0 ]; then
        log_info "No workflows found."
    else
        log_info "Total: $count workflow(s)"
    fi
}

# 特定ワークフローの状態を表示
workflow_state_show() {
    local task_id="$1"
    local state_file
    state_file=$(workflow_state_file "$task_id")

    if [ ! -f "$state_file" ]; then
        log_error "State file not found for task: $task_id"
        return 1
    fi

    local wf_name status base_branch created_at source_yaml
    wf_name=$(yq '.workflow-name' "$state_file")
    status=$(yq '.status' "$state_file")
    base_branch=$(yq '.base-branch' "$state_file")
    created_at=$(yq '.created-at' "$state_file")
    source_yaml=$(yq '.source-yaml' "$state_file")

    print_header "Workflow: $wf_name"
    log_info "Task ID: $task_id"
    log_info "Status: $status"
    log_info "Base branch: $base_branch"
    log_info "Source YAML: $source_yaml"
    log_info "Created at: $created_at"
    echo "" >&2

    local step_count
    step_count=$(yq '.steps | length' "$state_file")
    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local step_name step_status step_branch pr_url
        step_name=$(yq ".steps[$i].name" "$state_file")
        step_status=$(yq ".steps[$i].status" "$state_file")
        step_branch=$(yq ".steps[$i].branch" "$state_file")
        pr_url=$(yq ".steps[$i].pr-url" "$state_file")

        local status_icon
        case "$step_status" in
            pending)   status_icon="${DIM}○${RESET}" ;;
            running)   status_icon="${YELLOW}◉${RESET}" ;;
            completed) status_icon="${GREEN}●${RESET}" ;;
            skipped)   status_icon="${DIM}⊘${RESET}" ;;
            failed)    status_icon="${RED}✗${RESET}" ;;
            *)         status_icon="?" ;;
        esac

        local step_num
        step_num=$(printf "%02d" $((i + 1)))
        echo -e "  ${status_icon} Step ${step_num}: ${step_name}  [${step_status}]" >&2
        log_detail "Branch: $step_branch"
        if [ -n "$pr_url" ] && [ "$pr_url" != "" ] && [ "$pr_url" != "\"\"" ]; then
            log_detail "PR: $pr_url"
        fi

        i=$((i + 1))
    done
}

# --- 状態ファイルの削除 ---

workflow_state_remove() {
    local task_id="$1"
    local state_file
    state_file=$(workflow_state_file "$task_id")
    if [ -f "$state_file" ]; then
        rm "$state_file"
        log_info "Removed state file: $state_file"
    fi
}

# --- ステップの追加 ---

# ステップを動的に追加
# 引数: $1=task_id, $2=step_name, $3=description, $4=prompt
workflow_state_add_step() {
    local task_id="$1"
    local step_name="$2"

    local state_file
    state_file=$(workflow_state_file "$task_id")
    if [ ! -f "$state_file" ]; then
        log_error "State file not found for task: $task_id"
        return 1
    fi

    # 既存のステップ数から次のインデックスを算出
    local current_count
    current_count=$(yq '.steps | length' "$state_file")
    local next_index=$((current_count + 1))
    local step_num
    step_num=$(printf "%02d" "$next_index")

    local base_branch
    base_branch=$(yq '.base-branch' "$state_file")
    local branch_prefix="${base_branch%/base}"
    local step_branch="${branch_prefix}/step-${step_num}-${step_name}"
    local step_worktree=".worktrees/${branch_prefix}/step-${step_num}-${step_name}"

    yq -i ".steps += [{
        \"name\": \"$step_name\",
        \"index\": $next_index,
        \"branch\": \"$step_branch\",
        \"worktree\": \"$step_worktree\",
        \"status\": \"pending\",
        \"pr-url\": \"\"
    }]" "$state_file"

    log_success "Added step: $step_name (step-${step_num})"
}
