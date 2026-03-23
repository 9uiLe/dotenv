#!/bin/bash
#
# @PURPOSE: ワークフローエンジン本体（初期化、ステップ実行、クリーンアップ）
# @INPUT: なし（source で読み込む）
# @OUTPUT: エンジン関数をエクスポート
# @USAGE: source "$(dirname "$0")/lib/workflow-engine.sh"
# @CALLER: workflow-cli.sh
#

# --- 多重読み込み防止 ---
if [ -n "${_WORKFLOW_ENGINE_SH_LOADED:-}" ]; then
    return 0
fi
_WORKFLOW_ENGINE_SH_LOADED=1

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/workflow-yaml.sh"
source "$(dirname "${BASH_SOURCE[0]}")/workflow-state.sh"

# --- CLI パス解決 ---
_WORKFLOW_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_GIT_CLI="${_WORKFLOW_SCRIPT_DIR}/git-cli.sh"
_GH_CLI="${_WORKFLOW_SCRIPT_DIR}/gh-cli.sh"

# --- ワークフロー初期化 ---

# YAML からワークフローを初期化
# 引数: $1 = YAML ファイルパス
# 出力: task-id を stdout に出力
workflow_engine_init() {
    local yaml_file="$1"

    # バリデーション
    if ! workflow_yaml_validate "$yaml_file"; then
        return 1
    fi

    local wf_name base_ref prefix
    wf_name=$(workflow_yaml_get_name "$yaml_file")
    base_ref=$(workflow_yaml_get_base_branch "$yaml_file")
    prefix=$(workflow_yaml_get_branch_prefix "$yaml_file")

    # タスク ID 生成
    local timestamp slug task_id
    timestamp=$(generate_timestamp_id)
    slug=$(slugify "$wf_name")
    task_id="${timestamp}-${slug}"

    local branch_prefix="${prefix}/${task_id}"
    local base_branch="${branch_prefix}/base"

    log_step "Initializing workflow: $wf_name"
    log_detail "Task ID: $task_id"
    log_detail "Base branch: $base_branch"

    # リポジトリルートを取得
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)

    # master を最新に
    log_info "Fetching latest $base_ref..."
    "$_GIT_CLI" fetch-master

    # ベースブランチの worktree を作成（ブランチも同時に作成）
    local base_worktree=".worktrees/${branch_prefix}/base"
    log_info "Creating base worktree..."
    mkdir -p "$(dirname "${repo_root}/${base_worktree}")"
    (cd "$repo_root" && "$_GIT_CLI" worktree-add-new "$base_worktree" "$base_branch" "origin/$base_ref")

    # ベースブランチをリモートにプッシュ
    log_info "Pushing base branch to remote..."
    (cd "${repo_root}/${base_worktree}" && "$_GIT_CLI" push-upstream)

    # ステップ名を取得
    local step_names
    step_names=$(workflow_yaml_get_step_names "$yaml_file")

    # YAML ファイルの絶対パスを解決
    local abs_yaml
    abs_yaml=$(cd "$(dirname "$yaml_file")" && echo "$(pwd)/$(basename "$yaml_file")")

    # 状態ファイルを作成
    log_info "Creating state file..."
    workflow_state_create "$task_id" "$wf_name" "$abs_yaml" "$base_branch" "$step_names"

    log_success "Workflow initialized: $task_id"
    echo "$task_id"
}

# --- 依存関係解決 ---

# トポロジカルソートでステップの実行順序を決定
# 引数: $1 = YAML ファイルパス
# 出力: ステップ名を実行順で改行区切りに stdout に出力
workflow_engine_resolve_deps() {
    local yaml_file="$1"
    local step_count
    step_count=$(workflow_yaml_get_step_count "$yaml_file")

    # bash 3 互換のトポロジカルソート（連想配列を使わない）
    # ステップ名と依存関係をインデックス配列で管理
    local steps=()
    local deps_list=()

    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local name
        name=$(workflow_yaml_get_step_name "$yaml_file" "$i")
        steps+=("$name")
        local deps
        deps=$(workflow_yaml_get_step_deps "$yaml_file" "$i" 2>/dev/null | tr '\n' ',' )
        deps="${deps%,}"
        deps_list+=("$deps")
        i=$((i + 1))
    done

    local result=()
    local processed=()
    local remaining=$step_count
    local last_remaining=-1

    while [ "$remaining" -gt 0 ]; do
        if [ "$remaining" -eq "$last_remaining" ]; then
            log_error "Circular dependency detected in workflow steps"
            return 1
        fi
        last_remaining=$remaining

        local j=0
        while [ "$j" -lt "$step_count" ]; do
            local name="${steps[$j]}"
            local deps="${deps_list[$j]}"

            # 処理済みならスキップ
            local already_done=false
            local p
            for p in "${processed[@]}"; do
                if [ "$p" = "$name" ]; then
                    already_done=true
                    break
                fi
            done
            if [ "$already_done" = true ]; then
                j=$((j + 1))
                continue
            fi

            # 全依存が処理済みか確認
            local all_deps_done=true
            if [ -n "$deps" ]; then
                IFS=',' read -ra dep_arr <<< "$deps"
                local dep
                for dep in "${dep_arr[@]}"; do
                    local dep_found=false
                    for p in "${processed[@]}"; do
                        if [ "$p" = "$dep" ]; then
                            dep_found=true
                            break
                        fi
                    done
                    if [ "$dep_found" = false ]; then
                        all_deps_done=false
                        break
                    fi
                done
            fi

            if [ "$all_deps_done" = true ]; then
                result+=("$name")
                processed+=("$name")
                remaining=$((remaining - 1))
            fi

            j=$((j + 1))
        done
    done

    printf '%s\n' "${result[@]}"
}

# --- ステップ実行 ---

# 特定ステップの依存関係が解決済みか確認
# 引数: $1=task_id, $2=step_name, $3=yaml_file
workflow_engine_check_deps() {
    local task_id="$1"
    local step_name="$2"
    local yaml_file="$3"

    local step_count
    step_count=$(workflow_yaml_get_step_count "$yaml_file")

    # ステップのインデックスを探す
    local step_index=-1
    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local name
        name=$(workflow_yaml_get_step_name "$yaml_file" "$i")
        if [ "$name" = "$step_name" ]; then
            step_index=$i
            break
        fi
        i=$((i + 1))
    done

    if [ "$step_index" -eq -1 ]; then
        log_error "Step not found in YAML: $step_name"
        return 1
    fi

    # 依存関係を確認
    local deps
    deps=$(workflow_yaml_get_step_deps "$yaml_file" "$step_index" 2>/dev/null)
    if [ -z "$deps" ]; then
        return 0
    fi

    while IFS= read -r dep; do
        [ -z "$dep" ] && continue
        local dep_status
        dep_status=$(workflow_state_get_step_status "$task_id" "$dep")
        if [ "$dep_status" != "completed" ] && [ "$dep_status" != "skipped" ]; then
            log_error "Dependency not resolved: $dep (status: $dep_status)"
            return 1
        fi
    done <<< "$deps"

    return 0
}

# 単一ステップを実行
# ステップが pending → worktree を作成してプロンプトを表示
# ステップが running → コミットがあれば push して PR 作成
# 引数: $1=task_id, $2=step_name
workflow_engine_run_step() {
    local task_id="$1"
    local step_name="$2"

    if ! workflow_state_exists "$task_id"; then
        log_error "Workflow not found: $task_id"
        return 1
    fi

    local step_status
    step_status=$(workflow_state_get_step_status "$task_id" "$step_name")

    case "$step_status" in
        pending)
            _workflow_engine_setup_step "$task_id" "$step_name"
            ;;
        running)
            _workflow_engine_complete_step "$task_id" "$step_name"
            ;;
        completed)
            log_info "Step '$step_name' is already completed."
            ;;
        skipped)
            log_info "Step '$step_name' is skipped."
            ;;
        failed)
            log_warn "Step '$step_name' has failed. Re-running setup..."
            _workflow_engine_setup_step "$task_id" "$step_name"
            ;;
        *)
            log_error "Unknown step status: $step_status"
            return 1
            ;;
    esac
}

# ステップの worktree をセットアップしてプロンプトを表示
_workflow_engine_setup_step() {
    local task_id="$1"
    local step_name="$2"

    local source_yaml
    source_yaml=$(workflow_state_get_source_yaml "$task_id")

    # 依存関係チェック
    if ! workflow_engine_check_deps "$task_id" "$step_name" "$source_yaml"; then
        log_error "Cannot run step '$step_name': dependencies not resolved"
        return 1
    fi

    local step_branch step_worktree base_branch
    step_branch=$(workflow_state_get_step_branch "$task_id" "$step_name")
    step_worktree=$(workflow_state_get_step_worktree "$task_id" "$step_name")
    base_branch=$(workflow_state_get_base_branch "$task_id")

    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    local abs_worktree="${repo_root}/${step_worktree}"

    log_step "Setting up step: $step_name"

    # worktree が既に存在する場合はスキップ
    if [ -d "$abs_worktree" ]; then
        log_info "Worktree already exists: $step_worktree"
    else
        # 親ディレクトリを作成
        mkdir -p "$(dirname "$abs_worktree")"
        # worktree を作成（新しいブランチも作成）
        log_info "Creating worktree: $step_worktree"
        (cd "$repo_root" && "$_GIT_CLI" worktree-add-new "$step_worktree" "$step_branch" "$base_branch")
    fi

    # プロンプトを取得して表示
    local step_count
    step_count=$(workflow_yaml_get_step_count "$source_yaml")
    local step_index=-1
    local si=0
    while [ "$si" -lt "$step_count" ]; do
        local sn
        sn=$(workflow_yaml_get_step_name "$source_yaml" "$si")
        if [ "$sn" = "$step_name" ]; then
            step_index=$si
            break
        fi
        si=$((si + 1))
    done

    local prompt
    prompt=$(workflow_yaml_get_step_prompt "$source_yaml" "$step_index")

    # プロンプトファイルを worktree に保存
    echo "$prompt" > "${abs_worktree}/.workflow-prompt.md"

    # ステータスを running に更新
    workflow_state_set_step_status "$task_id" "$step_name" "running"

    # 情報を表示
    echo "" >&2
    print_header "Step: $step_name"
    log_info "Worktree: $abs_worktree"
    log_info "Branch: $step_branch"
    echo "" >&2
    log_step "Prompt:"
    echo "$prompt" >&2
    echo "" >&2
    log_info "Work in the worktree directory, commit changes, then run:"
    log_detail "workflow-cli.sh run-step $task_id $step_name"
    log_info "to push and create a PR."
}

# ステップを完了（push + PR 作成）
_workflow_engine_complete_step() {
    local task_id="$1"
    local step_name="$2"

    local step_branch step_worktree base_branch
    step_branch=$(workflow_state_get_step_branch "$task_id" "$step_name")
    step_worktree=$(workflow_state_get_step_worktree "$task_id" "$step_name")
    base_branch=$(workflow_state_get_base_branch "$task_id")

    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    local abs_worktree="${repo_root}/${step_worktree}"

    if [ ! -d "$abs_worktree" ]; then
        log_error "Worktree not found: $abs_worktree"
        log_error "Run 'run-step' first to set up the worktree."
        workflow_state_set_step_status "$task_id" "$step_name" "failed"
        return 1
    fi

    # ベースブランチとの差分を確認
    local diff_count
    diff_count=$(cd "$abs_worktree" && git log "$base_branch..HEAD" --oneline 2>/dev/null | wc -l | tr -d ' ')

    if [ "$diff_count" -eq 0 ]; then
        log_warn "No new commits in step '$step_name'."
        log_info "Work in the worktree and commit changes first:"
        log_detail "cd $abs_worktree"
        return 0
    fi

    log_step "Completing step: $step_name ($diff_count commit(s))"

    # push
    log_info "Pushing branch..."
    (cd "$abs_worktree" && "$_GIT_CLI" push-upstream)

    # PR 作成
    local source_yaml
    source_yaml=$(workflow_state_get_source_yaml "$task_id")
    local auto_pr
    auto_pr=$(workflow_yaml_get_auto_pr "$source_yaml")

    if [ "$auto_pr" = "true" ]; then
        log_info "Creating pull request..."
        local pr_title pr_body
        pr_title="[workflow] ${step_name}"
        pr_body="Workflow step: ${step_name}

Task: ${task_id}
Base: ${base_branch}"

        local labels assignee
        labels=$(workflow_yaml_get_pr_labels "$source_yaml" 2>/dev/null)
        assignee=$(workflow_yaml_get_pr_assignee "$source_yaml")

        local pr_args=(--base "$base_branch" --title "$pr_title" --body "$pr_body" --assignee "$assignee")
        if [ -n "$labels" ]; then
            while IFS= read -r label; do
                [ -z "$label" ] && continue
                pr_args+=(--label "$label")
            done <<< "$labels"
        fi

        local pr_url
        pr_url=$(cd "$abs_worktree" && "$_GH_CLI" pr-create "${pr_args[@]}")

        if [ -n "$pr_url" ]; then
            workflow_state_set_step_pr_url "$task_id" "$step_name" "$pr_url"
            log_success "PR created: $pr_url"
        else
            log_warn "PR creation returned no URL"
        fi
    else
        log_info "auto-pr is disabled. Skipping PR creation."
    fi

    # ステータスを completed に更新
    workflow_state_set_step_status "$task_id" "$step_name" "completed"
    log_success "Step '$step_name' completed."

    # 全ステップ完了チェック
    _workflow_engine_check_all_completed "$task_id"
}

# 全ステップが完了したかチェックし、ワークフロー全体を完了にする
_workflow_engine_check_all_completed() {
    local task_id="$1"
    local step_count
    step_count=$(workflow_state_get_step_count "$task_id")

    local completed=0
    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local status
        status=$(workflow_state_get "$task_id" ".steps[$i].status")
        case "$status" in
            completed|skipped) completed=$((completed + 1)) ;;
        esac
        i=$((i + 1))
    done

    if [ "$completed" -eq "$step_count" ]; then
        workflow_state_set_status "$task_id" "completed"
        log_success "All steps completed! Workflow '$task_id' is done."
    fi
}

# --- 全ステップ実行 ---

# 全 pending ステップを依存関係順に実行
workflow_engine_run() {
    local task_id="$1"

    if ! workflow_state_exists "$task_id"; then
        log_error "Workflow not found: $task_id"
        return 1
    fi

    local source_yaml
    source_yaml=$(workflow_state_get_source_yaml "$task_id")

    if [ ! -f "$source_yaml" ]; then
        log_error "Source YAML not found: $source_yaml"
        return 1
    fi

    # 依存関係解決
    local ordered_steps
    if ! ordered_steps=$(workflow_engine_resolve_deps "$source_yaml"); then
        return 1
    fi

    workflow_state_set_status "$task_id" "running"

    local ran_any=false
    while IFS= read -r step_name; do
        [ -z "$step_name" ] && continue

        local step_status
        step_status=$(workflow_state_get_step_status "$task_id" "$step_name")

        case "$step_status" in
            completed|skipped)
                log_info "Step '$step_name' already $step_status, skipping."
                continue
                ;;
            pending|running|failed)
                workflow_engine_run_step "$task_id" "$step_name"
                ran_any=true

                # pending から running になった場合、次のステップは依存待ちの可能性がある
                local new_status
                new_status=$(workflow_state_get_step_status "$task_id" "$step_name")
                if [ "$new_status" = "running" ]; then
                    log_info "Step '$step_name' is now running. Remaining steps will be processed on next run."
                    break
                fi
                ;;
        esac
    done <<< "$ordered_steps"

    if [ "$ran_any" = false ]; then
        log_info "No actionable steps found."
    fi
}

# --- クリーンアップ ---

# 特定ワークフローのクリーンアップ
workflow_engine_cleanup() {
    local task_id="$1"

    if ! workflow_state_exists "$task_id"; then
        log_error "Workflow not found: $task_id"
        return 1
    fi

    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    local base_branch
    base_branch=$(workflow_state_get_base_branch "$task_id")

    log_step "Cleaning up workflow: $task_id"

    # ステップの worktree を削除
    local step_count
    step_count=$(workflow_state_get_step_count "$task_id")
    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local step_worktree
        step_worktree=$(workflow_state_get "$task_id" ".steps[$i].worktree")
        if [ -d "${repo_root}/${step_worktree}" ]; then
            log_info "Removing worktree: $step_worktree"
            (cd "$repo_root" && "$_GIT_CLI" worktree-remove "$step_worktree") || true
        fi
        i=$((i + 1))
    done

    # ベースブランチの worktree を削除
    local branch_prefix="${base_branch%/base}"
    local base_worktree=".worktrees/${branch_prefix}/base"
    if [ -d "${repo_root}/${base_worktree}" ]; then
        log_info "Removing base worktree: $base_worktree"
        (cd "$repo_root" && "$_GIT_CLI" worktree-remove "$base_worktree") || true
    fi

    # worktree プルーニング
    (cd "$repo_root" && "$_GIT_CLI" worktree-prune) || true

    # worktree ディレクトリ自体が空であれば削除
    local wt_dir="${repo_root}/.worktrees/${branch_prefix}"
    if [ -d "$wt_dir" ]; then
        rmdir "$wt_dir" 2>/dev/null || true
    fi

    # 状態ファイルを削除
    workflow_state_remove "$task_id"

    log_success "Cleanup completed for: $task_id"
}

# 全ワークフローのクリーンアップ
workflow_engine_cleanup_all() {
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
    local state_dir="${repo_root}/${_WORKFLOW_STATE_DIR}"

    if [ ! -d "$state_dir" ]; then
        log_info "No workflows to clean up."
        return 0
    fi

    local count=0
    for state_file in "$state_dir"/*.yaml; do
        [ -f "$state_file" ] || continue
        local task_id
        task_id=$(yq '.task-id' "$state_file" 2>/dev/null)
        workflow_engine_cleanup "$task_id"
        count=$((count + 1))
    done

    # worktrees ディレクトリが空であれば削除
    if [ -d "${repo_root}/.worktrees" ]; then
        rmdir "${repo_root}/.worktrees" 2>/dev/null || true
    fi

    log_success "Cleaned up $count workflow(s)."
}
