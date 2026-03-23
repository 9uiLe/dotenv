#!/bin/bash
#
# @PURPOSE: ワークフロー実行 CLI。YAML ベースのタスク分解、worktree 分離実行、PR 自動作成を管理する
# @INPUT:
#   - $1: サブコマンド (describe, init, run, run-step, status, list, add-step, skip-step, cleanup, cleanup-all)
#   - $2...: サブコマンド固有のパラメータ
# @OUTPUT:
#   - 終了コード 0: 正常終了
#   - 終了コード 1: 不正なサブコマンドまたはパラメータ
#   - 終了コード 非0: 処理エラー
# @USAGE: ワークフローベースのタスク分解・実行管理
# @CALLER: workflow-manager Skill, workflow-executor Agent, 手動実行
# @EXAMPLE:
#   ./scripts/cli/workflow-cli.sh describe .workflow/templates/basic.yaml
#   ./scripts/cli/workflow-cli.sh init .workflow/templates/basic.yaml
#   ./scripts/cli/workflow-cli.sh status <task-id>
#   ./scripts/cli/workflow-cli.sh run <task-id>
#   ./scripts/cli/workflow-cli.sh run-step <task-id> <step-name>
#   ./scripts/cli/workflow-cli.sh list
#   ./scripts/cli/workflow-cli.sh cleanup <task-id>
#

source "$(dirname "$0")/lib/common.sh"
init_error_handling

source "$(dirname "$0")/lib/workflow-yaml.sh"
source "$(dirname "$0")/lib/workflow-state.sh"
source "$(dirname "$0")/lib/workflow-engine.sh"

# --- ヘルパー関数 ---

show_usage() {
    cat >&2 <<'USAGE'
Usage: workflow-cli.sh <subcommand> [params...]

Lifecycle:
  describe <yaml>              Validate and describe workflow YAML (dry-run)
  init <yaml>                  Initialize workflow (create base branch, state file)
  run <task-id>                Run all pending steps in dependency order
  run-step <task-id> <step>    Run or complete a specific step
  status <task-id>             Show workflow status
  list                         List all workflows

Step management:
  add-step <task-id> <name>    Add a new step to the workflow
  skip-step <task-id> <step>   Skip a step

Cleanup:
  cleanup <task-id>            Remove worktrees, branches, and state file
  cleanup-all                  Remove all workflow artifacts
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
    # ライフサイクル
    # =====================================================================

    describe)
        [ $# -ne 1 ] && { log_error "describe requires exactly one argument: <yaml>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_yaml_describe "$1"
        ;;

    init)
        [ $# -ne 1 ] && { log_error "init requires exactly one argument: <yaml>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_engine_init "$1"
        ;;

    run)
        [ $# -ne 1 ] && { log_error "run requires exactly one argument: <task-id>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_engine_run "$1"
        ;;

    run-step)
        [ $# -ne 2 ] && { log_error "run-step requires exactly two arguments: <task-id> <step-name>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_engine_run_step "$1" "$2"
        ;;

    status)
        [ $# -ne 1 ] && { log_error "status requires exactly one argument: <task-id>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_state_show "$1"
        ;;

    list)
        [ $# -ne 0 ] && { log_error "list takes no arguments."; exit 1; }
        require_command "yq" "brew install yq"
        workflow_state_list
        ;;

    # =====================================================================
    # ステップ管理
    # =====================================================================

    add-step)
        [ $# -ne 2 ] && { log_error "add-step requires exactly two arguments: <task-id> <step-name>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_state_add_step "$1" "$2"
        ;;

    skip-step)
        [ $# -ne 2 ] && { log_error "skip-step requires exactly two arguments: <task-id> <step-name>"; exit 1; }
        require_command "yq" "brew install yq"
        local_task_id="$1"
        local_step_name="$2"

        local_status=$(workflow_state_get_step_status "$local_task_id" "$local_step_name")
        case "$local_status" in
            pending|running|failed)
                workflow_state_set_step_status "$local_task_id" "$local_step_name" "skipped"
                log_success "Step '$local_step_name' skipped."
                ;;
            completed)
                log_error "Cannot skip a completed step: $local_step_name"
                exit 1
                ;;
            skipped)
                log_info "Step '$local_step_name' is already skipped."
                ;;
            *)
                log_error "Unknown step status: $local_status"
                exit 1
                ;;
        esac
        ;;

    # =====================================================================
    # クリーンアップ
    # =====================================================================

    cleanup)
        [ $# -ne 1 ] && { log_error "cleanup requires exactly one argument: <task-id>"; exit 1; }
        require_command "yq" "brew install yq"
        workflow_engine_cleanup "$1"
        ;;

    cleanup-all)
        [ $# -ne 0 ] && { log_error "cleanup-all takes no arguments."; exit 1; }
        require_command "yq" "brew install yq"
        workflow_engine_cleanup_all
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
