# 構造化入出力

[← 目次に戻る](./README.md)

---

## 1. JSON ペイロード優先

人間はネストされた JSON をターミナルに入力することを嫌うが、
エージェントはそれを好む。

フラグベースの入力（`--title "My Doc"`）は人間にとっては
直感的だが、ネスト構造を表現できず、
カスタムフラグの抽象化レイヤーが必要になる。

### Human-first: 10個のフラグ、フラットな名前空間

```bash
my-cli spreadsheet create \
  --title "Q1 Budget" \
  --locale "en_US" \
  --timezone "America/Denver" \
  --sheet-title "January" \
  --sheet-type GRID \
  --frozen-rows 1 \
  --frozen-cols 2 \
  --row-count 100 \
  --col-count 10 \
  --hidden false
```

### Agent-first: 1つのフラグで API ペイロードをそのまま受け付ける

```bash
gws sheets spreadsheets create --json '{
  "properties": {
    "title": "Q1 Budget",
    "locale": "en_US",
    "timeZone": "America/Denver"
  },
  "sheets": [{
    "properties": {
      "title": "January",
      "sheetType": "GRID",
      "gridProperties": {
        "frozenRowCount": 1,
        "frozenColumnCount": 2,
        "rowCount": 100,
        "columnCount": 10
      },
      "hidden": false
    }
  }]
}'
```

JSON 版は API スキーマに直接マッピングされ、
LLM が生成する際の変換ロスがゼロになる。

### 両立のための実践的アプローチ

Human DX と Agent DX のどちらかを選ぶ必要はない。
同一バイナリ内で両方のパスをサポートする:

| 方式 | 説明 |
|------|------|
| `--output json` フラグ | 出力形式の切り替え |
| `OUTPUT_FORMAT=json` 環境変数 | 環境レベルでの切り替え |
| stdout が TTY でない場合にデフォルト NDJSON | パイプ利用時に自動切替 |

---

## 2. ランタイムスキーマイントロスペクション

静的な API ドキュメントをシステムプロンプトに埋め込むのは
トークンコストが高く、API バージョン更新で即座に陳腐化する。

**より良いパターン**: CLI 自体をランタイムで問い合わせ可能な
ドキュメントにする。

```bash
gws schema drive.files.list
gws schema sheets.spreadsheets.create
```

各 `schema` コマンドは、パラメータ・リクエストボディ・
レスポンス型・必要な OAuth スコープを含む
メソッドシグネチャ全体を機械可読な JSON でダンプする。

### スキーマイントロスペクションの利点

| 従来の方式 | スキーマイントロスペクション |
|-----------|--------------------------|
| ドキュメントをプロンプトに埋め込み | CLI に問い合わせて取得 |
| API 更新で陳腐化 | 常に最新のスキーマ |
| トークン予算を大量消費 | 必要な分だけ取得 |
| 静的なテキスト | 構造化された JSON |

### 自前 CLI への適用

REST API をラップしない CLI でも、
`--describe` や `--help --json` で
コマンドが受け付けるパラメータのスキーマを
JSON で返す仕組みは有効。

```bash
# 例: 自前のスクリプトにスキーマ出力を追加
my-script --describe
# => {"params": {"name": {"type": "string", "required": true}, ...}}
```

---

## 3. コンテキストウィンドウ制御

API は巨大な JSON を返す。1通の Gmail メッセージだけで
エージェントのコンテキストウィンドウの有意な割合を
消費しうる。人間はスクロールすれば済むが、
**エージェントはトークンごとに課金され、
不要なフィールドで推論能力を失う**。

### Field Masks

必要なフィールドだけを返すようにする:

```bash
gws drive files list \
  --params '{"fields": "files(id,name,mimeType)"}'
```

### NDJSON ページネーション

`--page-all` はページごとに1つの JSON オブジェクトを出力し、
トップレベル配列をバッファリングせずに
ストリーム処理できるようにする。

### 実践ルール

エージェントはコンテキストウィンドウの節約を直感的に行えない。
**明示的にルール化する必要がある**:

> Workspace APIs return massive JSON blobs.
> ALWAYS use field masks when listing or getting resources
> by appending `--params '{"fields": "id,name"}'`
> to avoid overwhelming your context window.

このようなガイダンスは CLI の CONTEXT.md や
スキルファイルに記載しておく。

---

## 4. 出力設計のベストプラクティス

| プラクティス | 説明 |
|-------------|------|
| **JSON をデフォルトに** | 構造化出力を機械可読で返す |
| **`--compact` オプション** | トークン節約のため、不要な空白・改行を除去 |
| **エラーも構造化** | `{"error": {"code": "NOT_FOUND", "message": "..."}}` |
| **進捗は stderr へ** | stdout は純粋なデータ出力のみ |
| **NDJSON 対応** | 大量データはストリーム処理可能な形式で |

### stdout と stderr の分離

エージェントは stdout をパースする。
進捗表示やログメッセージは必ず stderr に出力し、
stdout を汚染しない:

```bash
# Good: データは stdout、進捗は stderr
echo '{"result": "success"}' # stdout
echo "Processing..." >&2     # stderr

# Bad: 全て stdout に混在
echo "Processing..."
echo '{"result": "success"}'
```
