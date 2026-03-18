---
paths: .claude/**
---

# Claude Code 設定変更ルール

## 概要

`.claude/` 配下のファイル（Rules、Skills、Agents、CLAUDE.md）を追加・変更・削除する際は、Anthropic 公式ドキュメントを参照して正しい実装方法を確認すること。

## 必須手順

### 1. 公式ドキュメントの確認

変更前に `claude-code-guide` エージェントを使用して、最新の公式ドキュメントを確認：

```text
Task tool で subagent_type=claude-code-guide を指定して実行
```

確認すべき内容：
- フロントマターの正しい構文
- サポートされているフィールド
- 配置場所とファイル命名規則
- 非推奨（Deprecated）になった機能がないか

### 2. 変更対象別の確認ポイント

| 対象 | 確認ポイント |
|------|-------------|
| **Rules** (`.claude/rules/*.md`) | paths パターン、フロントマター構文 |
| **Skills** (`.claude/skills/*/SKILL.md`) | allowed-tools、argument-hint フィールド。※プロジェクト固有のスキル実装パターンは `docs/skill-best-practices/` を参照 |
| **Agents** (`.claude/agents/*.md`) | tools、model、permissionMode フィールド |
| **CLAUDE.md** | 全体構成、参照方法 |

### 3. 変更後の検証

- 構文エラーがないか確認
- 意図通りに動作するかテスト

## 理由

Claude Code の設定は頻繁に更新されるため、ブログや過去の情報ではなく、常に公式ドキュメントを参照することで：

- 非推奨機能の使用を避ける
- 新機能を正しく活用する
- 設定ミスによるエラーを防ぐ
