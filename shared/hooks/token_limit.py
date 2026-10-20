#!/usr/bin/env python3
"""ステージ済みの手書きファイルを5,000トークン以下に保つ。"""

from fnmatch import fnmatchcase
import os
from pathlib import PurePosixPath
import subprocess
import sys


MAX_TOKENS = 5_000
MAX_BYTES = MAX_TOKENS * 4
LOCKFILES = {
    ".terraform.lock.hcl", "Cargo.lock", "Gemfile.lock", "Package.resolved",
    "Pipfile.lock", "Podfile.lock", "bun.lock", "bun.lockb", "composer.lock",
    "conan.lock", "deno.lock", "flake.lock", "go.sum", "mix.lock",
    "npm-shrinkwrap.json", "package-lock.json", "packages.lock.json",
    "pdm.lock", "pnpm-lock.yaml", "poetry.lock", "pubspec.lock", "uv.lock",
    "yarn.lock",
}
GENERATED_DIRECTORIES = {"generated", "__generated__", "vendor", "node_modules"}
GENERATED_FILENAMES = (
    "*.generated.*", "*.g.*", "*_pb2.py", "*_pb2_grpc.py", "*.pb.go",
    "*.min.js", "*.min.css",
)
IGNORE_ATTRIBUTES = ("token-limit-ignore", "linguist-generated", "linguist-vendored")


def git(*arguments, input=None):
    return subprocess.run(
        ["git", *arguments], input=input, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout


def staged_files():
    """リネームも追加として扱い、インデックスの通常blobだけを返す。"""
    records = git(
        "diff", "--cached", "--raw", "--no-abbrev", "--no-renames",
        "--diff-filter=ACMT", "-z", "--",
    ).split(b"\0")
    files = []
    for index in range(0, len(records) - 1, 2):
        metadata = records[index].split()
        mode, oid = metadata[1], metadata[3]
        if mode in (b"100644", b"100755"):
            files.append((records[index + 1], oid))
    return files


def known_generated(path):
    relative = PurePosixPath(os.fsdecode(path))
    return (
        relative.name in LOCKFILES
        or bool(GENERATED_DIRECTORIES.intersection(relative.parts[:-1]))
        or any(fnmatchcase(relative.name, pattern) for pattern in GENERATED_FILENAMES)
    )


def ignored_by_attributes(paths):
    """未ステージの.gitattributesによる判定の変化を避ける。"""
    records = git(
        "check-attr", "--cached", "-z", "--stdin", *IGNORE_ATTRIBUTES,
        input=b"\0".join(paths) + b"\0",
    ).split(b"\0")
    return {
        records[index]
        for index in range(0, len(records) - 1, 3)
        if records[index + 2] in (b"set", b"true")
    }


def main():
    files = [(path, oid) for path, oid in staged_files() if not known_generated(path)]
    if not files:
        return 0
    ignored = ignored_by_attributes([path for path, _ in files])
    files = [(path, oid) for path, oid in files if path not in ignored]
    if not files:
        return 0

    sizes = git(
        "cat-file", "--batch-check=%(objectsize)",
        input=b"\n".join(oid for _, oid in files) + b"\n",
    ).splitlines()
    oversized = [
        (path, int(size)) for (path, _), size in zip(files, sizes, strict=True)
        if int(size) > MAX_BYTES
    ]
    if not oversized:
        return 0

    print(
        "コミットを中止しました。ステージ済みファイルが上限の"
        f"{MAX_TOKENS:,}トークン（{MAX_BYTES:,}バイト）を超えています。\n"
        "計算式: トークン数 = バイト数 / 4（ファイル全体、端数も判定対象）。",
        file=sys.stderr,
    )
    for path, size in oversized:
        print(
            f"  {os.fsdecode(path)!r}: {size:,}バイト / {size / 4:,.2f}トークン",
            file=sys.stderr,
        )
    print(
        "\n分割して再コミットしてください:\n"
        "1. クラス・機能・責務ごとに別モジュールへ分割してください。\n"
        "   共通の型・補助関数・テストデータは専用ファイルへ移し、"
        "文書は章ごとに分けてください。\n"
        "2. import / include / 文書のリンクを更新し、動作を確認してください。\n"
        f"3. 分割後の各ファイルを{MAX_BYTES:,}バイト以下にし、元ファイルと"
        "新しいファイルを git add して、再度 git commit してください。\n"
        "機械生成物・外部由来ファイルは分割せず、.gitattributes に\n"
        "  path/to/generated-file token-limit-ignore\n"
        "を指定し、.gitattributes も git add してください。"
        "手書きファイルの制限回避には使わないでください。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError) as error:
        print(f"トークン上限の確認に失敗したため、コミットを中止しました: {error}",
              file=sys.stderr)
        sys.exit(1)
