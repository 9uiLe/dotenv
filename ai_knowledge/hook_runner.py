"""配布したHookを照合し、排他制御のもとで実行する。"""

import argparse
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

try:
    from .sandbox import check_home
except ImportError:
    from sandbox import check_home


PHASES = {'ready', 'applying', 'recovery_required', 'uninstalled'}


def _safe_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('hook path contains a symbolic link')


def _regular_file(path: Path) -> int:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError('hook path is not a regular file')
    return descriptor


def _bundle_relative(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('release file path is invalid')
    relative = PurePosixPath(name)
    if (relative.is_absolute() or '..' in relative.parts
            or str(relative) != name or len(relative.parts) < 2
            or relative.parts[0] not in ('skills', 'hooks')):
        raise ValueError('release file path escapes its allowed directory')
    return relative


def _check_content(descriptor: int, expected: str) -> None:
    if not isinstance(expected, str):
        raise ValueError('release file content is invalid')
    expected_bytes = expected.encode('utf-8')
    with os.fdopen(os.dup(descriptor), 'rb') as content:
        actual = content.read(len(expected_bytes) + 1)
    if actual != expected_bytes:
        raise ValueError('release file differs from the active bundle')


def _inactive() -> int:
    print('{}')
    return 0


def run(home: Path, tool: str, hook_id: str, *, home_guard=check_home,
        max_state_bytes=None) -> int:
    home = home_guard(home)
    if tool not in ('codex', 'claude'):
        raise ValueError('unknown hook tool')
    data = home / '.local/share/ai-knowledge'
    state_path = data / 'state.json'
    lock_path = data / 'operation.lock'
    _safe_path(state_path)
    _safe_path(lock_path)
    if not state_path.exists():
        return _inactive()
    if not data.is_dir():
        raise ValueError('hook state directory is invalid')
    # The manager holds LOCK_EX for every change; the shared lock covers the child.
    lock_descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise ValueError('hook lock is not a regular file')
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return _inactive()
        # Re-check after acquiring the lock so the manager's completed state wins.
        home_guard(home)
        if not state_path.exists():
            return _inactive()
        with os.fdopen(_regular_file(state_path), encoding='utf-8') as state_file:
            if (max_state_bytes is not None
                    and os.fstat(state_file.fileno()).st_size > max_state_bytes):
                raise ValueError('hook state exceeds the installed runtime size limit')
            if max_state_bytes is None:
                state = json.load(state_file)
            else:
                raw = state_file.buffer.read(max_state_bytes + 1)
                if len(raw) > max_state_bytes:
                    raise ValueError('hook state exceeds the installed runtime size limit')
                state = json.loads(raw.decode('utf-8'))
        if not isinstance(state, dict) or state.get('phase') not in PHASES:
            raise ValueError('hook state phase is invalid')
        if not isinstance(state.get('hooks_enabled'), bool):
            raise ValueError('hook state enabled flag is invalid')
        active = state.get('active')
        if active is not None and not isinstance(active, dict):
            raise ValueError('hook active state is invalid')
        if state['phase'] != 'ready' or not state['hooks_enabled'] or active is None:
            return _inactive()
        revision = active.get('revision')
        bundle = active.get('bundle')
        if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision):
            raise ValueError('hook revision is invalid')
        if not isinstance(bundle, dict) or not isinstance(bundle.get('hooks'), list):
            raise ValueError('hook bundle is invalid')
        matches = []
        for hook in bundle['hooks']:
            if not isinstance(hook, dict):
                raise ValueError('hook definition is invalid')
            if hook.get('id') == hook_id:
                matches.append(hook)
        if len(matches) != 1:
            raise ValueError('unknown or duplicate hook id')
        hook = matches[0]
        tools = hook.get('tools')
        if not isinstance(tools, list) or tool not in tools:
            raise ValueError('hook is not enabled for this tool')
        script = hook.get('script')
        relative = _bundle_relative(script)
        if relative.parts[0] != 'hooks':
            raise ValueError('hook script must be inside hooks')
        files = bundle.get('files')
        if not isinstance(files, dict) or not isinstance(files.get(script), str):
            raise ValueError('hook script content is missing from the bundle')
        timeout = hook.get('timeout')
        if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('hook timeout is invalid')
        release = data / 'releases' / revision
        script_path = release.joinpath(*relative.parts)
        _safe_path(script_path)
        # A held file descriptor keeps the validated script inode stable at launch.
        script_descriptor = _regular_file(script_path)
        try:
            for name, expected in files.items():
                file_relative = _bundle_relative(name)
                if name == script:
                    _check_content(script_descriptor, expected)
                else:
                    descriptor = _regular_file(release.joinpath(*file_relative.parts))
                    try:
                        _check_content(descriptor, expected)
                    finally:
                        os.close(descriptor)
            os.lseek(script_descriptor, 0, os.SEEK_SET)
            result = subprocess.run(
                [sys.executable, '/dev/fd/' + str(script_descriptor)],
                input=sys.stdin.buffer.read(), capture_output=True,
                timeout=timeout, shell=False, pass_fds=(script_descriptor,),
                cwd=home,
            )
        finally:
            os.close(script_descriptor)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        return result.returncode if result.returncode >= 0 else 1
    finally:
        os.close(lock_descriptor)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--tool', choices=('codex', 'claude'), required=True)
    parser.add_argument('--hook', required=True)
    arguments = parser.parse_args(argv)
    try:
        return run(arguments.home, arguments.tool, arguments.hook)
    except subprocess.TimeoutExpired:
        print('shared hook timed out', file=sys.stderr)
    except (OSError, ValueError, TypeError, OverflowError):
        print('shared hook state or path is invalid', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
