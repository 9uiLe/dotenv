"""PC共通のGitフックを検証し、既存フックへ処理を引き継ぐ。"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import sys

try:
    from .sandbox import check_home
except ImportError:
    from sandbox import check_home


HOOK_EVENTS = (
    'applypatch-msg', 'pre-applypatch', 'post-applypatch', 'pre-commit',
    'pre-merge-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit',
    'pre-rebase', 'post-checkout', 'post-merge', 'pre-push', 'pre-receive',
    'update', 'proc-receive', 'post-receive', 'post-update',
    'reference-transaction', 'push-to-checkout', 'pre-auto-gc', 'post-rewrite',
    'sendemail-validate', 'fsmonitor-watchman', 'p4-changelist',
    'p4-prepare-changelist', 'p4-post-changelist', 'p4-pre-submit',
    'post-index-change',
)
DATA = '.local/share/ai-knowledge'
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
PHASES = {'ready', 'applying', 'recovery_required', 'uninstalled'}


def _safe_path(path):
    """管理対象の構成要素だけを調べ、無関係なホームを走査しない。"""
    for candidate in (path, *path.parents):
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError('管理対象のパスが通常ファイル・フォルダではありません')


def _open_regular(path):
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError('管理対象が通常ファイルではありません')
    return descriptor


def _read(path, limit):
    with os.fdopen(_open_regular(path), 'rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('管理ファイルが読み取り上限を超えています')
    return content


def _document(path, limit=MAX_METADATA_BYTES):
    return json.loads(_read(path, limit).decode('utf-8'))


def _installation(home):
    """Gitのcwdと環境を保持したまま、実行するランタイムだけを照合する。"""
    data = home / DATA
    document = _document(data / 'installation.json')
    if (not isinstance(document, dict) or document.get('schema') != 1
            or document.get('home') != str(home)
            or document.get('python') != str(Path(sys.executable).absolute())
            or document.get('profile') not in {'personal', 'work'}
            or document.get('mode') not in {'live', 'mock'}):
        raise ValueError('導入情報が不正です')
    if document['mode'] == 'live':
        if (platform.system() != 'Darwin' or os.getuid() == 0
                or os.getuid() != os.geteuid()
                or home != Path(pwd.getpwuid(os.getuid()).pw_dir)):
            raise ValueError('導入したMacの通常ユーザーだけが実行できます')
        metadata = data.stat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ValueError('管理フォルダは本人所有・権限700にしてください')
    else:
        if home.name != document['profile']:
            raise ValueError('模擬環境とプロファイルが一致しません')
        check_home(home, scan_tree=False)
    runtime = data / 'runtime'
    if Path(__file__).absolute() != runtime / 'ai_knowledge/git_dispatch.py':
        raise ValueError('導入済みランタイムの場所が一致しません')
    hashes = document.get('runtime_hashes')
    if not isinstance(hashes, dict) or 'ai_knowledge/git_dispatch.py' not in hashes:
        raise ValueError('ランタイムの照合情報がありません')
    for name, expected in hashes.items():
        relative = Path(name)
        if (len(relative.parts) != 2 or relative.parts[0] != 'ai_knowledge'
                or relative.suffix != '.py' or str(relative) != name
                or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
            raise ValueError('ランタイムの照合情報が不正です')
        actual = hashlib.sha256(_read(runtime / relative, MAX_METADATA_BYTES)).hexdigest()
        if actual != expected:
            raise ValueError('導入済みランタイムが変更されています。再導入してください')


def _exit_code(result):
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def _token_check(home):
    data = home / DATA
    state_path = data / 'state.json'
    _safe_path(state_path)
    if not state_path.exists():
        raise ValueError('Gitフックの管理状態がありません。共有設定を再導入してからコミットしてください')
    _installation(home)
    lock_path = data / 'operation.lock'
    _safe_path(lock_path)
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ValueError('管理ロックが通常ファイルではありません')
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('共有設定の更新中です。完了後にコミットを再実行してください') from error
        state = _document(state_path, MAX_STATE_BYTES)
        if (not isinstance(state, dict) or state.get('schema') != 1
                or state.get('phase') not in PHASES or type(state.get('hooks_enabled')) is not bool):
            raise ValueError('Gitフックの管理状態が不正です')
        active = state.get('active')
        if state['phase'] == 'applying':
            raise ValueError('共有設定の適用中です。完了後にコミットを再実行してください')
        if state['phase'] != 'ready' or not state['hooks_enabled'] or active is None:
            return 0
        if not isinstance(active, dict):
            raise ValueError('配布中の共有設定が不正です')
        bundle, revision = active.get('bundle'), active.get('revision')
        if not isinstance(bundle, dict) or not isinstance(revision, str):
            raise ValueError('配布中の版が不正です')
        canonical = json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
        if (not re.fullmatch('[0-9a-f]{64}', revision)
                or hashlib.sha256(canonical.encode('utf-8')).hexdigest() != revision):
            raise ValueError('配布中の版と共有設定が一致しません')
        files = bundle.get('files')
        if not isinstance(files, dict):
            raise ValueError('共有ファイルの配布情報が不正です')
        script = files.get('hooks/token_limit.py')
        # 旧版にはこのGitフックが存在しないため、その版の既存フックだけを実行する。
        if script is None:
            return 0
        if not isinstance(script, str):
            raise ValueError('トークン検査の配布内容が不正です')
        expected = script.encode('utf-8')
        script_path = data / 'releases' / revision / 'hooks/token_limit.py'
        descriptor = _open_regular(script_path)
        try:
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                if stream.read(len(expected) + 1) != expected:
                    raise ValueError('トークン検査の実体と配布内容が一致しません')
            os.lseek(descriptor, 0, os.SEEK_SET)
            # Gitの一時インデックスも含め、cwd・GIT_*・標準出力をそのまま渡す。
            result = subprocess.run(
                [sys.executable, '/dev/fd/' + str(descriptor)],
                stdin=subprocess.DEVNULL, pass_fds=(descriptor,), timeout=60,
            )
            return _exit_code(result)
        finally:
            os.close(descriptor)
    finally:
        os.close(lock)


def _configured_previous(home, fallback):
    """includeIfの条件を現在のリポジトリで評価し、管理includeだけを除く。"""
    environment = dict(os.environ, HOME=str(home))
    environment.setdefault('XDG_CONFIG_HOME', str(home / '.config'))
    try:
        result = subprocess.run(
            ['git', 'config', '--includes', '--show-scope', '--show-origin', '-z',
             '--get-all', 'core.hooksPath'],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError('既存のGit設定を15秒以内に読み取れませんでした') from error
    if result.returncode == 1:
        return fallback
    if result.returncode:
        raise ValueError('既存のGit設定を読み取れません')
    records = result.stdout.split(b'\0')
    if records[-1] != b'' or (len(records) - 1) % 3:
        raise ValueError('既存のGit設定の形式が不正です')
    previous = None
    managed = home / DATA / 'git-config'
    managed_hooks = home / DATA / 'git-hooks'
    for index in range(0, len(records) - 1, 3):
        origin, value = os.fsdecode(records[index + 1]), os.fsdecode(records[index + 2])
        if origin.startswith('file:') and Path(origin[5:]).absolute() == managed:
            continue
        if _hook_directory(home, value).resolve() == managed_hooks.resolve():
            continue
        previous = value
    return previous


def _hook_directory(home, value):
    return (home / value[2:] if value.startswith('~/') else
            home if value == '~' else Path(os.path.expanduser(value)))


def _previous_hook(home, previous, event):
    previous = _configured_previous(home, previous)
    if previous is None:
        result = subprocess.run(['git', 'rev-parse', '--git-common-dir'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            common = Path(os.fsdecode(result.stdout.removesuffix(b'\n')))
        elif os.environ.get('GIT_COMMON_DIR'):
            common = Path(os.environ['GIT_COMMON_DIR'])
        elif os.environ.get('GIT_DIR'):
            # git initのreference-transactionでは、HEAD作成前のためrev-parseが失敗する。
            common = Path(os.environ['GIT_DIR'])
            commondir = common / 'commondir'
            if commondir.is_file():
                common = common / os.fsdecode(commondir.read_bytes().removesuffix(b'\n'))
        else:
            raise ValueError('既存Gitフックの場所を確認できません')
        directory = common / 'hooks'
    else:
        # 相対パスはGitがフックを起動したcwdに対して解決する。
        directory = _hook_directory(home, previous)
    hook = directory / event
    managed = home / DATA / 'git-hooks'
    if (directory.resolve() == managed.resolve()
            or hook.resolve() == (managed / event).resolve()):
        return None
    return hook if hook.is_file() and os.access(hook, os.X_OK) else None


def run(home, previous, event, arguments, *, check_only=False):
    if event not in HOOK_EVENTS or (previous is not None and not isinstance(previous, str)):
        raise ValueError('Gitフックの起動引数が不正です')
    home = Path(home).absolute()
    if event == 'pre-commit':
        result = _token_check(home)
        if result:
            return result
    if check_only:
        if event != 'pre-commit':
            raise ValueError('検査専用の起動はpre-commitだけに使用できます')
        return 0
    hook = _previous_hook(home, previous, event)
    if hook is None:
        return 0
    try:
        result = subprocess.run([str(hook.absolute()), *arguments])
    except OSError as error:
        if error.errno != errno.ENOEXEC:
            raise
        # Gitと同様に、shebangのない既存シェルフックも実行する。
        result = subprocess.run(['/bin/sh', str(hook.absolute()), *arguments])
    return _exit_code(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--previous-hooks-path-json', required=True)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('event', choices=HOOK_EVENTS)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        return run(args.home, json.loads(args.previous_hooks_path_json), args.event,
                   args.arguments, check_only=args.check_only)
    except subprocess.TimeoutExpired:
        print('トークン検査が60秒以内に完了しなかったため、コミットを中止しました。', file=sys.stderr)
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError) as error:
        print(f'PC共通Gitフックを実行できないため、処理を中止しました: {error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
