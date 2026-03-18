# 入力バリデーション

[← 目次に戻る](./README.md)

---

## 1. ハルシネーション対策の必要性

人間はタイポする。エージェントはハルシネーションする。
**失敗モードはまったく異なる。**

| 脅威 | 人間の場合 | エージェントの場合 |
|------|-----------|------------------|
| パス走査 | `../../.ssh` を偶然入力 — まず起きない | パスセグメントを混同して生成 — 起こりうる |
| クエリパラメータ混入 | ほぼ発生しない | リソース ID 内に `?fields=name` を埋め込む — 実績あり |
| 二重エンコード | ほぼ発生しない | URL エンコード済み文字列を再エンコード — よくある |
| 制御文字 | コピペでゴミが入る程度 | 不可視文字を文字列内に生成 |

> **"Agents hallucinate. Build like it."**
>
> エージェントは信頼できるオペレータではない。
> Web API がユーザー入力を信頼せずに検証するのと同様に、
> CLI もエージェントの入力を信頼してはならない。

---

## 2. 検証パターン一覧

### ファイルパスの検証

エージェントはパスセグメントを混同して
`../../.ssh` のようなパス走査を生成しうる。
出力先を正規化してサンドボックス化する:

```python
def validate_safe_output_dir(path: str) -> str:
    """出力パスを CWD 内にサンドボックス化する"""
    resolved = os.path.realpath(path)
    cwd = os.path.realpath(os.getcwd())
    if not resolved.startswith(cwd):
        raise ValueError(
            f"Path traversal detected: {path}"
        )
    return resolved
```

```bash
# シェルスクリプトでの簡易的な実装
validate_path() {
  local resolved
  resolved="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  if [[ "$resolved" != "$PWD"* ]]; then
    echo "Error: Path traversal detected: $1" >&2
    return 1
  fi
}
```

### 制御文字の拒否

エージェントが不可視文字を出力に含める場合がある。
ASCII 0x20 未満の文字（改行・タブを除く）を拒否する:

```python
def reject_control_chars(value: str) -> str:
    """制御文字を含む入力を拒否する"""
    for char in value:
        code = ord(char)
        if code < 0x20 and char not in ('\n', '\t', '\r'):
            raise ValueError(
                f"Control character detected: "
                f"U+{code:04X}"
            )
    return value
```

### リソース ID の検証

エージェントはリソース ID 内にクエリパラメータを
埋め込むことがある（例: `fileId?fields=name`）。
`?`, `#`, `%` を含む ID を拒否する:

```python
def validate_resource_name(resource_id: str) -> str:
    """リソース ID にクエリパラメータ等が含まれていないか検証"""
    invalid_chars = {'?', '#', '%'}
    for char in invalid_chars:
        if char in resource_id:
            raise ValueError(
                f"Invalid character '{char}' "
                f"in resource ID: {resource_id}"
            )
    return resource_id
```

### URL エンコードの検証

エージェントは事前にエンコード済みの文字列を渡すことがあり、
二重エンコードが発生する（`%2e%2e` → `%252e%252e`）。
入力段階で `%` を拒否し、
エンコードは HTTP レイヤーで一元管理する:

```python
def encode_path_segment(segment: str) -> str:
    """パスセグメントを HTTP レイヤーでエンコードする。
    事前エンコードされた入力は拒否する。"""
    if '%' in segment:
        raise ValueError(
            f"Pre-encoded input detected: {segment}"
        )
    return urllib.parse.quote(segment, safe='')
```

---

## 3. 防御的設計の原則

### Enum による制約

自由テキスト入力を可能な限り列挙型で制約する:

```bash
# Bad: 自由テキスト
my-cli create --type "whatever-the-agent-hallucinates"

# Good: 列挙型で制約
my-cli create --type GRID  # GRID | OBJECT のみ受付
```

### スキーマバリデーション

JSON 入力を受け付ける場合は、
JSON Schema でバリデーションを行い、
不正な入力を早期に拒否する。

### 冪等操作の優先

可能な限り操作を冪等にする。
エージェントが同じコマンドを複数回実行しても
副作用が増大しないようにする。

### エラーメッセージの構造化

エラーメッセージもエージェントが処理できるよう構造化する:

```json
{
  "error": {
    "code": "INVALID_RESOURCE_ID",
    "message": "Resource ID contains invalid character '?'",
    "field": "fileId",
    "suggestion": "Remove query parameters from the resource ID"
  }
}
```

---

## 4. 入力バリデーションのチェックリスト

- [ ] ファイルパスを CWD 内にサンドボックス化しているか
- [ ] 制御文字（ASCII 0x20 未満）を拒否しているか
- [ ] リソース ID に `?`, `#`, `%` が含まれていないか検証しているか
- [ ] URL エンコードは HTTP レイヤーで一元管理しているか
- [ ] 列挙型で制約可能な入力は Enum にしているか
- [ ] JSON 入力に対するスキーマバリデーションがあるか
- [ ] エラーメッセージが構造化されているか
