# 要素別ベストプラクティス

[← 目次に戻る](./README.md)

---

## 1. テーブル

### 使用する場面

テーブルは **均一なデータの比較・一覧** に使用する。説明文や長い文章にはテーブルを使用しない。

```markdown
<!-- Good（均一データの一覧） -->
| フィールド | 必須 | 説明 |
|-----------|------|------|
| name | 必須 | スキル名 |
| description | 必須 | 説明文 |

<!-- Bad（長い説明をテーブルに入れている） -->
| 項目 | 説明 |
|------|------|
| 概要 | このスキルはシェルスクリプトを作成するためのもので、対話的な要件収集から始まり、テンプレート生成、品質検証、連携登録までの一連のフローを提供します。 |
```

### テーブルの書式

- ヘッダー行とデータ行の間に区切り行（`|------|`）を入れる
- セルの内容が短い場合でもパイプ（`|`）の位置を揃える
- テーブルの前後に空行を入れる

```markdown
<!-- Good（揃っている） -->
| 項目   | 内容         |
|--------|-------------|
| 名前   | テスト       |
| 説明   | サンプル     |

<!-- Bad（揃っていない） -->
|項目|内容|
|---|---|
|名前|テスト|
|説明|サンプル|
```

---

## 2. リスト

### 順序なしリスト

関連する項目の列挙にはハイフン（`-`）を使用する。アスタリスク（`*`）やプラス（`+`）は使用しない。

```markdown
<!-- Good -->
- 項目 A
- 項目 B
- 項目 C

<!-- Bad -->
* 項目 A
+ 項目 B
* 項目 C
```

### 順序付きリスト

手順やステップには番号付きリストを使用する。長いリストでは「lazy numbering」（全て `1.`）を推奨する。

```markdown
<!-- Good（lazy numbering - 並び替え時に番号変更不要） -->
1. 最初のステップ
1. 次のステップ
1. 最後のステップ

<!-- Good（短いリストなら連番も可） -->
1. 最初のステップ
2. 次のステップ
3. 最後のステップ

<!-- Bad（lazy numbering と連番の混在） -->
1. 最初のステップ
2. 次のステップ
1. 最後のステップ
```

### タスクリスト

チェックリストにはタスクリスト構文を使用する。

```markdown
- [x] 完了した項目
- [ ] 未完了の項目
- [ ] 未完了の項目
```

---

## 3. コードブロック

### フェンス形式で言語を指定する

コードブロックにはバッククォート3つのフェンス形式を使用し、言語を指定する。インデントによるコードブロックは使用しない。

````markdown
<!-- Good（言語指定あり） -->
```swift
let name = "Hello"
```

```yaml
name: my-skill
description: 説明文
```

```bash
#!/bin/bash
echo "Hello"
```

<!-- Bad（言語指定なし） -->
```
let name = "Hello"
```
````

### よく使用する言語識別子

| 言語 | 識別子 |
|------|--------|
| Swift | `swift` |
| YAML | `yaml` |
| Bash / Shell | `bash` |
| Markdown | `markdown` |
| JSON | `json` |
| 指定なし（プレーンテキスト） | `text` |

### インラインコード

短いコード、コマンド、ファイルパスにはインラインコード（`` ` ``）を使用する。

```markdown
<!-- Good -->
`CLAUDE.md` を更新してください。
`swift build` コマンドを実行します。

<!-- Bad -->
CLAUDE.md を更新してください。
swift build コマンドを実行します。
```

### 長いコマンドラインのバックスラッシュエスケープ

コードブロック内の長いコマンドラインは、バックスラッシュ（`\`）で改行をエスケープして可読性を高める。

````markdown
<!-- Good（バックスラッシュで改行） -->
```bash
xcodebuild \
    -workspace App.xcworkspace \
    -scheme MyApp \
    -destination 'platform=iOS Simulator,name=iPhone 16' \
    clean build
```

<!-- Bad（1行に詰め込む） -->
```bash
xcodebuild -workspace App.xcworkspace -scheme MyApp -destination 'platform=iOS Simulator,name=iPhone 16' clean build
```
````

---

## 4. リンク

### 説明的なテキストを使用する

リンクテキストには説明的な文言を使用する。「こちら」「ここ」などの曖昧な表現は使用しない。

```markdown
<!-- Good -->
[スキル ベストプラクティスガイド](./docs/skill-best-practices/README.md) を参照してください。

<!-- Bad -->
ベストプラクティスは [こちら](./docs/skill-best-practices/README.md) を参照。
```

### URL の直接記載を避ける

URL をそのまま記載するのではなく、リンク構文を使用する。

```markdown
<!-- Good -->
[Google Markdown Style Guide](https://google.github.io/styleguide/docguide/style.html)

<!-- Bad -->
https://google.github.io/styleguide/docguide/style.html
```

### 相対パスの使用

同一リポジトリ内のファイルへのリンクには相対パスを使用する。

```markdown
<!-- Good（相対パス） -->
[目次に戻る](./README.md)
[データフローガイド](../data-flow/README.md)

<!-- Bad（絶対パスや URL） -->
[目次に戻る](/docs/markdown-best-practices/README.md)
```

### 長い URL には参照リンクを使用する

インラインリンクの URL が長くソースの可読性が下がる場合は、参照リンクスタイル（reference-style link）を使用する。

```markdown
<!-- Good（参照リンク - ソースが読みやすい） -->
詳細は [Google Markdown Style Guide][google-md] を参照。
設計方針は [Swift API Design Guidelines][swift-api] に準拠する。

[google-md]: https://google.github.io/styleguide/docguide/style.html
[swift-api]: https://www.swift.org/documentation/api-design-guidelines/

<!-- Good（短い URL ならインラインでも可） -->
[目次に戻る](./README.md)
```

参照リンクの定義は以下のいずれかに配置する:

- リンクが使用されているセクションの末尾（次の見出しの直前）
- ドキュメントの末尾

> [!NOTE]
> 同一リポジトリ内の相対リンクは URL が短いため、インラインリンクで問題ない。参照リンクは主に外部 URL が長い場合に使用する。

---

## 5. 画像

### 画像の使用は控えめにする

画像はテキストで表現しにくい内容に限定して使用する。テキストで十分説明できる内容には画像を使わない。

画像を使用する場合の指針:

- シンプルなスクリーンショットを推奨（不要な装飾を避ける）
- 画面全体ではなく、該当部分のみを切り出す
- テキストで代替できる場合はテキストを優先する

```markdown
<!-- Good（テキストで説明できる内容はテキストで） -->
**設定 > 一般 > 言語と地域** から言語を変更する。

<!-- Bad（テキストで済む内容にスクリーンショットを使用） -->
![設定画面のスクリーンショット](./images/settings.png)
```

### 代替テキストを必ず記載する

画像には代替テキスト（alt text）を記載する。

```markdown
<!-- Good -->
![アーキテクチャ図: レイヤー間のデータフロー](./images/architecture.png)

<!-- Bad（代替テキストなし） -->
![](./images/architecture.png)
```

---

## 6. アラート（GitHub 拡張構文）

### 適切なアラートレベルの選択

| アラート | 用途 |
|---------|------|
| `> [!NOTE]` | 補足情報、参考になる追加情報 |
| `> [!TIP]` | 便利なヒント、ショートカット |
| `> [!IMPORTANT]` | 重要な情報、見落とすべきでない内容 |
| `> [!WARNING]` | 注意が必要な潜在的な問題 |
| `> [!CAUTION]` | 重大なリスク、データ損失の可能性 |

```markdown
> [!NOTE]
> この設定は開発環境でのみ有効です。

> [!WARNING]
> この操作は元に戻せません。実行前にバックアップを取得してください。
```

---

## 7. 区切り線

### セクション間の視覚的な区切り

チャプターファイル内の大きなセクション間には区切り線（`---`）を使用する。

```markdown
## セクション 1

内容...

---

## セクション 2

内容...
```

### 使用しない場面

README.md やトップレベルのドキュメントでは、見出しだけで十分な区切りとなるため、区切り線は不要。

---

## 8. HTML の使用禁止

### Markdown を優先する

HTML タグではなく Markdown 構文を使用する。

```markdown
<!-- Good -->
**太字テキスト**

<!-- Bad -->
<b>太字テキスト</b>

<!-- Good -->
![画像](./image.png)

<!-- Bad -->
<img src="./image.png" />
```
