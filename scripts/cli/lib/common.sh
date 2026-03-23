#!/bin/bash
#
# @PURPOSE: scripts/cli/ 配下の全スクリプトで共有するカラー定義・ログ関数・ユーティリティ
# @INPUT: なし（source で読み込む）
# @OUTPUT: カラー変数、ログ関数、ユーティリティ関数をエクスポート
# @USAGE: source "$(dirname "$0")/lib/common.sh"
# @CALLER: scripts/cli/ 配下の全スクリプト
#

# --- 多重読み込み防止 ---
if [ -n "${_COMMON_SH_LOADED:-}" ]; then
    return 0
fi
_COMMON_SH_LOADED=1

# --- カラー定義（統一） ---
BOLD='\033[1m'
DIM='\033[2m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[97m'
RESET='\033[0m'
NC="${RESET}"  # cleanup-claude-branches.sh 互換エイリアス

# --- ログ関数 ---
log_info()    { echo -e "${BLUE}[INFO]${RESET}    $*" >&2; }
log_success() { echo -e "${GREEN}[SUCCESS]${RESET} $*" >&2; }
log_warn()    { echo -e "${YELLOW}[WARN]${RESET}    $*" >&2; }
log_error()   { echo -e "${RED}[ERROR]${RESET}   $*" >&2; }
log_step()    { echo -e "${CYAN}${BOLD}▶${RESET} $*" >&2; }
log_detail()  { echo -e "  ${DIM}$*${RESET}" >&2; }

# --- ヘッダー/フッター ---
print_header() {
    local title="$1"
    echo "" >&2
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}" >&2
    echo -e "${BOLD}  ${title}${RESET}" >&2
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}" >&2
    echo "" >&2
}

print_footer() {
    local message="${1:-処理が完了しました!}"
    echo "" >&2
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}" >&2
    log_success "$message"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}" >&2
    echo "" >&2
}

# --- プロジェクト共通定数 ---
WORKSPACE_NAME="UsapoApp.xcworkspace"

# --- ユーティリティ ---

# 呼び出し元スクリプトのディレクトリからプロジェクトルートを解決する
resolve_project_root() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    cd "$script_dir/../.." && pwd
}

# エラーハンドリングの初期化
# 引数: "pipefail" を渡すと set -o pipefail も有効化
init_error_handling() {
    set -e
    if [ "${1:-}" = "pipefail" ]; then
        set -o pipefail
    fi
}

# 必須引数のバリデーション
require_arg() {
    local value="$1"
    local name="$2"
    local usage="${3:-}"
    if [ -z "$value" ]; then
        log_error "Required argument missing: ${name}"
        if [ -n "$usage" ]; then
            echo "$usage" >&2
        fi
        exit 1
    fi
}

# 経過時間の計測ユーティリティ
now_sec() {
    date +%s
}

elapsed_sec() {
    local start="$1"
    echo $(( $(date +%s) - start ))
}

# 必須コマンドの存在確認
require_command() {
    local cmd="$1"
    local install_hint="${2:-}"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command not found: ${cmd}"
        if [ -n "$install_hint" ]; then
            log_error "Install with: ${install_hint}"
        fi
        exit 1
    fi
}

# タイムスタンプベースの ID を生成
generate_timestamp_id() {
    date +"%Y%m%d-%H%M%S"
}

# 文字列をスラッグ化（小文字、ハイフン区切り）
slugify() {
    local result
    result=$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -c '[:alnum:]\n' '-' | tr -s '-')
    result="${result#-}"
    result="${result%-}"
    echo "$result"
}
