---
paths: **/*.md
---

# Markdown ベストプラクティス参照ルール

## 概要

Markdown ファイル（`.md`）の作成・変更時に、プロジェクト固有の Markdown 書式規約について `docs/markdown-best-practices/` のドキュメントを参照すること。
Google Markdown Style Guide と GitHub Markdown Syntax の推奨事項を統合したガイドであり、見出し、テーブル、リスト、コードブロック等の書式の一貫性を保つ。

## 必須手順

### 参照すべきドキュメントの選択

作業内容に応じて、以下のドキュメントを参照すること:

| 作業内容 | 参照ドキュメント | パス |
|---------|----------------|------|
| 見出し、テキストスタイル、空行、インデントの書式 | 基本的な書式ルール | `docs/markdown-best-practices/01-formatting.md` |
| ドキュメント構造、セクション構成、ナビゲーション | ドキュメント構造パターン | `docs/markdown-best-practices/02-structure.md` |
| テーブル、リスト、コードブロック、リンク、画像の書き方 | 要素別ベストプラクティス | `docs/markdown-best-practices/03-elements.md` |
| Markdown の品質確認、レビュー | レビューチェックリスト | `docs/markdown-best-practices/04-checklist.md` |

### 参照方法

1. 上記テーブルから作業内容に該当するドキュメントを特定
2. `Read` ツールで該当ドキュメントを読み込む
3. ドキュメントに記載されたパターン・規約に従って実装

### 全体像の把握が必要な場合

Markdown ベストプラクティスの全体像を把握する必要がある場合は、まず `docs/markdown-best-practices/README.md` を参照すること。ドキュメント一覧と各ファイルの役割が記載されている。

## 理由

- Markdown の書式（見出しスタイル、リスト記号、インデント幅等）はプロジェクト内で統一することで可読性が向上する
- テーブル、コードブロック、リンクの使い方にはベストプラクティスがあり、一貫した適用が重要
- ドキュメントには具体的な Good/Bad 例が含まれており、正確な実装の参考になる
