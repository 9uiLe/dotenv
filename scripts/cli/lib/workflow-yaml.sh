#!/bin/bash
#
# @PURPOSE: ワークフロー YAML ファイルのパース・バリデーション層（yq ラッパー）
# @INPUT: なし（source で読み込む）
# @OUTPUT: YAML パース関数をエクスポート
# @USAGE: source "$(dirname "$0")/lib/workflow-yaml.sh"
# @CALLER: workflow-cli.sh
#

# --- 多重読み込み防止 ---
if [ -n "${_WORKFLOW_YAML_SH_LOADED:-}" ]; then
    return 0
fi
_WORKFLOW_YAML_SH_LOADED=1

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# --- YAML バリデーション ---

# ワークフロー YAML ファイルのバリデーション
# 引数: $1 = YAML ファイルパス
# 戻り値: 0=有効, 1=無効（エラーメッセージは stderr に出力）
workflow_yaml_validate() {
    local yaml_file="$1"

    if [ ! -f "$yaml_file" ]; then
        log_error "YAML file not found: $yaml_file"
        return 1
    fi

    require_command "yq" "brew install yq"

    local errors=0

    # 必須フィールドの存在チェック
    local required_fields=("name" "description" "branch.base" "branch.prefix" "steps")
    for field in "${required_fields[@]}"; do
        local value
        value=$(yq ".$field" "$yaml_file" 2>/dev/null)
        if [ -z "$value" ] || [ "$value" = "null" ]; then
            log_error "Missing required field: $field"
            errors=$((errors + 1))
        fi
    done

    # steps が配列であることを確認
    local step_count
    step_count=$(yq '.steps | length' "$yaml_file" 2>/dev/null)
    if [ -z "$step_count" ] || [ "$step_count" = "0" ]; then
        log_error "steps must be a non-empty array"
        errors=$((errors + 1))
    else
        # 各ステップの必須フィールドを確認
        local i=0
        while [ "$i" -lt "$step_count" ]; do
            local step_name
            step_name=$(yq ".steps[$i].name" "$yaml_file" 2>/dev/null)
            if [ -z "$step_name" ] || [ "$step_name" = "null" ]; then
                log_error "Step $i: missing required field 'name'"
                errors=$((errors + 1))
            fi

            local step_desc
            step_desc=$(yq ".steps[$i].description" "$yaml_file" 2>/dev/null)
            if [ -z "$step_desc" ] || [ "$step_desc" = "null" ]; then
                log_error "Step $i ($step_name): missing required field 'description'"
                errors=$((errors + 1))
            fi

            local step_prompt
            step_prompt=$(yq ".steps[$i].prompt" "$yaml_file" 2>/dev/null)
            if [ -z "$step_prompt" ] || [ "$step_prompt" = "null" ]; then
                log_error "Step $i ($step_name): missing required field 'prompt'"
                errors=$((errors + 1))
            fi

            # depends-on のバリデーション（存在する場合、参照先が有効か確認）
            local dep_count
            dep_count=$(yq ".steps[$i].depends-on | length" "$yaml_file" 2>/dev/null)
            if [ -n "$dep_count" ] && [ "$dep_count" != "null" ] && [ "$dep_count" -gt 0 ] 2>/dev/null; then
                local d=0
                while [ "$d" -lt "$dep_count" ]; do
                    local dep_name
                    dep_name=$(yq ".steps[$i].depends-on[$d]" "$yaml_file" 2>/dev/null)
                    # 依存先がステップ一覧に存在するか確認
                    local found
                    found=$(yq ".steps[].name | select(. == \"$dep_name\")" "$yaml_file" 2>/dev/null)
                    if [ -z "$found" ]; then
                        log_error "Step $i ($step_name): depends-on references unknown step '$dep_name'"
                        errors=$((errors + 1))
                    fi
                    d=$((d + 1))
                done
            fi

            i=$((i + 1))
        done

        # ステップ名の重複チェック
        local unique_count
        unique_count=$(yq '.steps[].name' "$yaml_file" 2>/dev/null | sort -u | wc -l | tr -d ' ')
        if [ "$unique_count" != "$step_count" ]; then
            log_error "Duplicate step names detected"
            errors=$((errors + 1))
        fi
    fi

    if [ "$errors" -gt 0 ]; then
        log_error "Validation failed with $errors error(s)"
        return 1
    fi

    return 0
}

# --- ゲッター関数 ---

# ワークフロー名を取得
workflow_yaml_get_name() {
    yq '.name' "$1" 2>/dev/null
}

# ワークフロー説明を取得
workflow_yaml_get_description() {
    yq '.description' "$1" 2>/dev/null
}

# ベースブランチを取得
workflow_yaml_get_base_branch() {
    yq '.branch.base' "$1" 2>/dev/null
}

# ブランチプレフィックスを取得
workflow_yaml_get_branch_prefix() {
    yq '.branch.prefix' "$1" 2>/dev/null
}

# auto-pr 設定を取得（デフォルト: true）
workflow_yaml_get_auto_pr() {
    local value
    value=$(yq '.settings.auto-pr // true' "$1" 2>/dev/null)
    echo "$value"
}

# PR ラベルを取得（改行区切り）
workflow_yaml_get_pr_labels() {
    yq '.settings.pr-labels[]' "$1" 2>/dev/null
}

# PR assignee を取得
workflow_yaml_get_pr_assignee() {
    yq '.settings.pr-assignee // "@me"' "$1" 2>/dev/null
}

# ステップ数を取得
workflow_yaml_get_step_count() {
    yq '.steps | length' "$1" 2>/dev/null
}

# 全ステップ名を取得（改行区切り）
workflow_yaml_get_step_names() {
    yq '.steps[].name' "$1" 2>/dev/null
}

# 特定ステップの名前を取得
workflow_yaml_get_step_name() {
    local yaml_file="$1"
    local index="$2"
    yq ".steps[$index].name" "$yaml_file" 2>/dev/null
}

# 特定ステップの説明を取得
workflow_yaml_get_step_description() {
    local yaml_file="$1"
    local index="$2"
    yq ".steps[$index].description" "$yaml_file" 2>/dev/null
}

# 特定ステップのプロンプトを取得
workflow_yaml_get_step_prompt() {
    local yaml_file="$1"
    local index="$2"
    yq ".steps[$index].prompt" "$yaml_file" 2>/dev/null
}

# 特定ステップの依存関係を取得（改行区切り）
workflow_yaml_get_step_deps() {
    local yaml_file="$1"
    local index="$2"
    yq ".steps[$index].depends-on[]" "$yaml_file" 2>/dev/null
}

# --- 表示関数 ---

# ワークフロー YAML の内容を人間向けに表示
workflow_yaml_describe() {
    local yaml_file="$1"

    if ! workflow_yaml_validate "$yaml_file"; then
        return 1
    fi

    local name desc base_branch prefix step_count
    name=$(workflow_yaml_get_name "$yaml_file")
    desc=$(workflow_yaml_get_description "$yaml_file")
    base_branch=$(workflow_yaml_get_base_branch "$yaml_file")
    prefix=$(workflow_yaml_get_branch_prefix "$yaml_file")
    step_count=$(workflow_yaml_get_step_count "$yaml_file")

    print_header "Workflow: $name"
    log_info "Description: $desc"
    log_info "Base branch: $base_branch"
    log_info "Branch prefix: $prefix"
    log_info "Steps: $step_count"
    echo "" >&2

    local i=0
    while [ "$i" -lt "$step_count" ]; do
        local step_name step_desc
        step_name=$(workflow_yaml_get_step_name "$yaml_file" "$i")
        step_desc=$(workflow_yaml_get_step_description "$yaml_file" "$i")
        local deps
        deps=$(workflow_yaml_get_step_deps "$yaml_file" "$i" 2>/dev/null)

        local step_num
        step_num=$(printf "%02d" $((i + 1)))
        log_step "Step $step_num: $step_name"
        log_detail "Description: $step_desc"
        if [ -n "$deps" ]; then
            log_detail "Depends on: $(echo "$deps" | tr '\n' ', ' | sed 's/,$//')"
        else
            log_detail "Depends on: (none)"
        fi
        echo "" >&2
        i=$((i + 1))
    done

    # Settings
    local auto_pr assignee
    auto_pr=$(workflow_yaml_get_auto_pr "$yaml_file")
    assignee=$(workflow_yaml_get_pr_assignee "$yaml_file")
    log_info "Settings:"
    log_detail "Auto PR: $auto_pr"
    log_detail "PR Assignee: $assignee"
    local labels
    labels=$(workflow_yaml_get_pr_labels "$yaml_file" 2>/dev/null)
    if [ -n "$labels" ]; then
        log_detail "PR Labels: $(echo "$labels" | tr '\n' ', ' | sed 's/,$//')"
    fi
}
