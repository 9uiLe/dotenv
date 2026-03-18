# シェルスクリプト ベストプラクティス オフラインリファレンス

頻出のベストプラクティスをバンドルしたオフラインリファレンス。
Web 検索前にまずこのファイルを参照し、ここで回答できる内容は Web 検索をスキップする。

## 目次

1. [エラーハンドリング](#1-エラーハンドリング)
2. [変数と引数](#2-変数と引数)
3. [パフォーマンス](#3-パフォーマンス)
4. [スタイルと命名](#4-スタイルと命名)
5. [移植性（macOS/Linux）](#5-移植性macoslinux)
6. [よくある ShellCheck 警告と修正](#6-よくある-shellcheck-警告と修正)

---

## 1. エラーハンドリング

### set オプション

```bash
set -euo pipefail
```

| オプション | 効果 |
|-----------|------|
| `-e` | コマンド失敗時にスクリプト終了 |
| `-u` | 未定義変数の参照でエラー |
| `-o pipefail` | パイプライン内のコマンド失敗を検出 |

### trap によるクリーンアップ

```bash
cleanup() {
    rm -f "$TMPFILE"
}
trap cleanup EXIT
```

- `EXIT` トラップは正常終了・エラー終了の両方で呼ばれる
- `trap cleanup EXIT INT TERM` で割り込みシグナルもカバー
- 一時ファイルは `mktemp` で作成し、trap で確実に削除

### エラーメッセージの構造

```bash
log_error "Failed to build module '${module}': exit code ${rc}"
log_error "  Expected: ${expected_path}"
log_error "  Hint: Run 'xcodebuild clean' and retry"
```

- 何が起きたか（事実）
- 何を期待していたか（コンテキスト）
- どう対処すべきか（ヒント）

---

## 2. 変数と引数

### クォーティング

```bash
# 常にダブルクォートで囲む
echo "$variable"
cp "$source" "$destination"

# 意図的に分割が必要な場合のみクォートなし
# shellcheck disable=SC2086
flags=$user_flags
```

### デフォルト値

```bash
# デフォルト値の設定
OUTPUT_DIR="${OUTPUT_DIR:-build/output}"
RETRY_COUNT="${RETRY_COUNT:-3}"
VERBOSE="${VERBOSE:-false}"
```

### 引数バリデーション

```bash
if [[ $# -lt 1 ]]; then
    echo "Usage: $(basename "$0") <module-name> [--verbose]" >&2
    exit 1
fi
```

### readonly で定数を保護

```bash
readonly PROJECT_ROOT
readonly CONFIG_PATH="$PROJECT_ROOT/.config"
```

---

## 3. パフォーマンス

### 組み込みコマンド vs 外部コマンド

| 推奨（組み込み） | 避けるべき（外部） | 理由 |
|----------------|-----------------|------|
| `[[ ]]` | `[ ]` + 外部 test | Bash 組み込みで高速 |
| `${var%%pattern}` | `sed`/`awk` | サブシェル不要 |
| `${var//old/new}` | `sed 's/old/new/g'` | fork/exec 不要 |
| `printf` | `echo` の多用 | 移植性とフォーマット制御 |

### ファイル読み取り

```bash
# 良い: リダイレクトで読み取り（サブシェル回避）
while IFS= read -r line; do
    process "$line"
done < "$file"

# 悪い: パイプでサブシェル生成
cat "$file" | while IFS= read -r line; do
    process "$line"  # サブシェル内なので変数変更が反映されない
done
```

### 一括処理

```bash
# 良い: find -exec + で一括
find . -name "*.sh" -exec shellcheck {} +

# 良い: xargs で一括
find . -name "*.sh" -print0 | xargs -0 shellcheck

# 悪い: ループ内で毎回コマンド起動
for file in $(find . -name "*.sh"); do
    shellcheck "$file"
done
```

### 不要なプロセスの排除

```bash
# 良い: 直接リダイレクト
grep "pattern" < "$file"
wc -l < "$file"

# 悪い: Useless Use of Cat
cat "$file" | grep "pattern"
cat "$file" | wc -l
```

---

## 4. スタイルと命名

### 関数名

- 小文字 + アンダースコア: `process_files`, `validate_input`
- 動詞から始める: `build_module`, `check_status`, `cleanup_artifacts`

### 変数名

- ローカル変数: 小文字 + アンダースコア: `local file_path`
- 環境変数・定数: 大文字 + アンダースコア: `OUTPUT_DIR`, `MAX_RETRY`

### 関数定義

```bash
# 推奨: function キーワードなし
my_function() {
    local arg="$1"
    # ...
}

# 非推奨: function キーワードあり（POSIX 非互換）
function my_function {
    # ...
}
```

### コメント

```bash
# ファイルヘッダー: スクリプトの目的を説明
# 関数コメント: 複雑なロジックに「なぜ」を説明
# TODO コメント: 未実装や改善点を明記
```

---

## 5. 移植性（macOS/Linux）

本プロジェクトは macOS（BSD 系）が主対象。以下の差異に注意:

| 機能 | macOS (BSD) | Linux (GNU) |
|------|-------------|-------------|
| `sed -i` | `sed -i '' 's/...'` | `sed -i 's/...'` |
| `readlink -f` | 未対応（`realpath` または自前実装） | 対応 |
| `date` | BSD date（`-v` オプション） | GNU date（`-d` オプション） |
| `grep -P` | 未対応 | 対応（PCRE） |
| `mktemp` | `mktemp -t prefix` | `mktemp --tmpdir prefix.XXXX` |
| `find -regex` | BSD 正規表現 | GNU 拡張正規表現 |

### 推奨パターン

```bash
# readlink -f の代替
resolve_path() {
    echo "$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
}

# OS 判定
case "$(uname -s)" in
    Darwin) SED_INPLACE=(-i '') ;;
    Linux)  SED_INPLACE=(-i) ;;
esac
sed "${SED_INPLACE[@]}" 's/old/new/' "$file"
```

---

## 6. よくある ShellCheck 警告と修正

| コード | 説明 | 修正方法 |
|--------|------|---------|
| SC2086 | ダブルクォート不足 | `"$var"` でクォート |
| SC2155 | declare と assign を分離 | `local x; x=$(cmd)` |
| SC2034 | 未使用変数 | 変数を使用するか削除 |
| SC2164 | `cd` の失敗未処理 | `cd dir \|\| exit 1` |
| SC2006 | バッククォート使用 | `$(cmd)` に置換 |
| SC2046 | クォートなし `$()` | `"$(cmd)"` でクォート |
| SC2162 | `read` に `-r` なし | `read -r var` |
| SC2181 | `$?` チェック | `if cmd; then` に置換 |
| SC2236 | `-n`/`-z` 不使用 | `[[ -n "$var" ]]` |

---

## 出典

- GNU Bash Reference Manual: https://www.gnu.org/software/bash/manual/
- Google Shell Style Guide: https://google.github.io/styleguide/shellguide.html
- ShellCheck Wiki: https://www.shellcheck.net/wiki/
- BashFAQ (Greg's Wiki): https://mywiki.wooledge.org/BashFAQ
