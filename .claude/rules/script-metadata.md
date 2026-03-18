---
paths: scripts/cli/**
---

# Script Metadata Rules（スクリプトメタデータルール）

## 概要

`scripts/cli/` 配下のスクリプトには、メタデータヘッダーを追加すること。
これにより、スクリプトの目的・入出力・用途を動的に把握できる。

## メタデータフォーマット

シェルスクリプトの先頭に以下の形式でメタデータを記載:

```bash
#!/bin/bash
#
# @PURPOSE: スクリプトの目的（1行で簡潔に）
# @INPUT:
#   - arg1: 引数の説明
#   - ENV_VAR: 環境変数の説明 (default: 値)
# @OUTPUT:
#   - 終了コード 0: 成功
#   - 終了コード 非0: 失敗
# @USAGE: どのような場面で使用するか
# @CALLER: 呼び出し元の SubAgent/Skill 名
# @EXAMPLE: 実行例（オプション）
#
```

## フィールド説明

| フィールド | 必須 | 説明 |
|-----------|------|------|
| @PURPOSE | ✅ | スクリプトの目的（1行） |
| @INPUT | ✅ | 引数と環境変数（なければ "なし" と記載） |
| @OUTPUT | ✅ | 終了コードと出力の説明 |
| @USAGE | ✅ | 使用場面の説明 |
| @CALLER | ✅ | 呼び出し元の SubAgent/Skill 名 |
| @EXAMPLE | - | 実行例（複雑なスクリプトの場合に推奨） |

## チェックリスト

スクリプト作成・変更時に確認:

- [ ] `@PURPOSE` が1行で簡潔に書かれているか
- [ ] `@INPUT` で全ての引数・環境変数が説明されているか
- [ ] `@OUTPUT` で終了コードの意味が明記されているか
- [ ] `@USAGE` で使用場面が明確か
- [ ] `@CALLER` で呼び出し元が正しく記載されているか

## 例

```bash
#!/bin/bash
#
# @PURPOSE: プロジェクトをビルドしてエラーを検出する
# @INPUT:
#   - CLEAN_BUILD: クリーンビルドを実行する (default: false)
#   - AUTO_RETRY_CLEAN: 失敗時にクリーンビルドで自動リトライ (default: true)
# @OUTPUT:
#   - 終了コード 0: ビルド成功
#   - 終了コード 非0: ビルド失敗（コンパイルエラーあり）
# @USAGE: コード変更後のビルド検証、PR作成前の確認
# @CALLER: build-verify Agent
# @EXAMPLE: CLEAN_BUILD=true ./scripts/cli/build-verify.sh
#
```
