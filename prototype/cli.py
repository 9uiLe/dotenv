"""Human-readable JSON commands for the generated sandbox only."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path


def parser():
    result = argparse.ArgumentParser(
        prog="python3 -m prototype",
        description=(
            "模擬環境専用の共有設定デモ。更新予定は各Macの現地時刻で毎日10:00。"
            "共有元はGitHubまたはローカルの模擬データ。Gitへの送信、実際の設定変更、"
            "常駐処理の登録は行いません。"
        ),
        epilog=(
            "初回: python3 -m prototype init\n"
            "操作: python3 -m prototype --sandbox PATH --pc personal sync "
            "--at 2026-10-20T10:00:00+09:00\n"
            "--sandbox と --pc はサブコマンドの前に指定します。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    result.add_argument("--sandbox", type=Path, help="init が作った模擬環境のパス")
    result.add_argument(
        "--pc", choices=("personal", "work"), default="personal",
        help="対象の模擬Mac（初期値: personal）",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="架空の共有元と2台の模擬Macを新規作成")
    sync = commands.add_parser("sync", help="日次更新の1回分を模擬実行")
    sync.add_argument(
        "--at", type=_datetime,
        help="模擬時刻（ISO 8601。省略時は現在の現地時刻）",
    )
    sync.add_argument("--force", action="store_true", help="10:00や当日実行済みの判定を省く")
    commands.add_parser("status", help="模擬環境の状態を確認")
    configure = commands.add_parser("configure-git", help="模擬MacのGit受信元を保存（通信なし）")
    configure.add_argument("--url", required=True)
    configure.add_argument("--branch", required=True)
    configure.add_argument("--manifest", default="shared/manifest.json")
    commands.add_parser("preview-sync", help="候補を受信して差分表示（配布先と適用済み状態は変更しない）")
    projection = commands.add_parser("preview-install", help="指定ホームを読み取り、未適用の導入案を別の模擬環境へ保存")
    projection.add_argument("--read-home", type=Path, required=True)
    projection.add_argument("--source-url", required=True, help="本番の受信元候補（この操作では接続しない）")
    projection.add_argument("--source-branch", required=True)
    commands.add_parser("pause-sync", help="模擬Macの受信・反映を停止")
    commands.add_parser("resume-sync", help="模擬Macの受信・反映を再開")
    commands.add_parser("pause-hooks", help="共有Hookの実行を停止")
    commands.add_parser("resume-hooks", help="共有Hookの実行を再開")
    restore = commands.add_parser("restore", help="共通部分を前の版へ戻す（初期値はプレビュー）")
    restore.add_argument("--apply", action="store_true", help="模擬環境に復元を適用")
    uninstall = commands.add_parser("uninstall", help="導入前へ戻す（初期値はプレビュー）")
    uninstall.add_argument("--apply", action="store_true", help="模擬環境に導入取り消しを適用")
    return result


def _datetime(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--at は ISO 8601 の日時で指定してください") from error
    # A timezone-free input means the computer's local timezone, matching the schedule.
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _write_sample(root, bundle):
    """Create source without following a symlink in any path component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open(root.anchor, flags)
    source_fd = None
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        os.mkdir("source", dir_fd=current)
        source_fd = os.open("source", flags, dir_fd=current)
        file_fd = os.open(
            "bundle.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=source_fd,
        )
        with os.fdopen(file_fd, "w", encoding="utf-8") as sample:
            json.dump(bundle, sample, ensure_ascii=False, indent=2)
            sample.write("\n")
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(current)


def _run(args):
    if args.command == "init":
        if args.sandbox is not None:
            raise ValueError("init は新しい模擬環境を作ります。--sandbox は指定しないでください")
        from .fixtures import make_bundle
        from .sandbox import create_sandbox

        root = create_sandbox(prefix="manual-")
        _write_sample(root, make_bundle(version="v1"))
        return {
            "outcome": "created",
            "scope": "offline mock homes only",
            "sandbox": str(root),
            "source": str(root / "source" / "bundle.json"),
            "profiles": ["personal", "work"],
            "daily_update": "10:00 (each Mac's local time)",
        }

    if args.sandbox is None:
        raise ValueError("先に init を実行し、--sandbox PATH を指定してください")

    from .engine import Manager

    manager = Manager(args.sandbox / args.pc, args.sandbox / "source", args.pc)
    if args.command == "configure-git":
        from .git_source import validate_config
        from .engine import encoded, write_atomic

        configuration = {"url": args.url, "branch": args.branch, "manifest": args.manifest}
        validate_config(manager.home, configuration)
        with manager._lock():
            write_atomic(manager.source / 'git.json', encoded(configuration))
        return {"outcome": "configured", "source": configuration, "fetched": False}
    if args.command == "preview-sync":
        result = manager.preview_sync()
        result.pop('bundle', None)
        return result
    if args.command == "preview-install":
        from .preflight import project_installation
        from .git_source import validate_config

        source = {'url': args.source_url, 'branch': args.source_branch,
                  'manifest': 'shared/manifest.json'}
        validate_config(manager.home, source)
        candidate = manager.preview_sync()
        if candidate['outcome'] != 'preview':
            return candidate
        return project_installation(args.read_home, candidate['bundle'], source)
    if args.command == "sync":
        return manager.sync(now=args.at or datetime.now().astimezone(), force=args.force)
    if args.command == "restore":
        return manager.restore(previous=True, apply=args.apply)
    if args.command == "uninstall":
        return manager.uninstall(apply=args.apply)
    return getattr(manager, args.command.replace("-", "_"))()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = _run(args)
    except Exception as error:
        result = {"outcome": "failed", "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("outcome") == "failed" else 0
