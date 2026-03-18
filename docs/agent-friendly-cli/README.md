# AI エージェント向け CLI 設計ガイド

## 概要

AI エージェントが主要な利用者となる CLI ツールの設計原則と
実装パターンをまとめたガイド。
Justin Poehnelt 氏の
"[You Need to Rewrite Your CLI for AI Agents](https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/)"
を基に、補足情報を加えて再構成したもの。

### Human DX と Agent DX の違い

| 観点 | Human DX | Agent DX |
|------|----------|----------|
| 最適化対象 | 発見しやすさ（discoverability）と寛容さ | 予測可能性と多層防御 |
| 入力方式 | フラグベース、インタラクティブ | 構造化 JSON ペイロード |
| 出力方式 | カラー表示、テーブル整形 | 機械可読 JSON |
| ドキュメント | `--help`、Web ドキュメント | ランタイムスキーマイントロスペクション |
| エラー処理 | 人間が読むエラーメッセージ | 構造化エラーコード + 検証結果 |

**核心**: Human DX と Agent DX は対立するものではなく直交している。
人間向けの利便性を維持しつつ、エージェントが必要とする
構造化パス・スキーマ・入力バリデーション・安全機構を
その下に構築する。

## 対象読者

- `scripts/cli/` 配下のシェルスクリプトを設計・開発する場合
- CLI ツールをエージェント対応に改修する場合
- MCP サーバーやツール統合の設計を行う場合

## ドキュメント一覧

| ファイル | 内容 |
|----------|------|
| [01-structured-io.md](./01-structured-io.md) | 構造化入出力（JSON ペイロード、スキーマ、コンテキストウィンドウ制御） |
| [02-input-hardening.md](./02-input-hardening.md) | 入力バリデーション（ハルシネーション対策、防御的検証） |
| [03-skills-and-surfaces.md](./03-skills-and-surfaces.md) | スキルファイル設計とマルチサーフェス公開（MCP、拡張、環境変数） |
| [04-safety-rails.md](./04-safety-rails.md) | 安全機構（dry-run、レスポンスサニタイゼーション、プロンプトインジェクション対策） |
| [05-migration-checklist.md](./05-migration-checklist.md) | 既存 CLI のエージェント対応チェックリスト |

## 関連

- `docs/skill-best-practices/` - スキル設計のベストプラクティス
- `.claude/skills/script-creator/SKILL.md` - スクリプト作成スキル
- `scripts/cli/` - 本プロジェクトの CLI スクリプト群
