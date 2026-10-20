"""Receive committed knowledge into a sandbox cache without a working checkout."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess

from ai_knowledge.sandbox import check_home


MAX_MANIFEST = 64 * 1024
MAX_BLOB = 1024 * 1024
MAX_TOTAL = 5 * 1024 * 1024
GIT_TIMEOUT = 20
_CACHE_CONFIG = ('[core]\n\trepositoryformatversion = 0\n'
                 '\tfilemode = true\n\tbare = true\n')
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')


class ReceiveError(ValueError):
    """A short error that deliberately excludes Git's potentially sensitive output."""


def _relative(name):
    if (not isinstance(name, str) or not name or '\\' in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise ReceiveError('共有元の相対パスが不正です')
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or str(path) != name
            or name == '.' or len(name.encode('utf-8')) > 4096):
        raise ReceiveError('共有元の相対パスが不正です')
    return path


def _endpoint(home, url, allow_local=True):
    if not isinstance(url, str):
        raise ReceiveError('受信元URLが不正です')
    local = home.parent / 'remote.git'
    if allow_local and url == str(local):
        if not local.is_dir():
            raise ReceiveError('模擬Git受信元が見つかりません')
        return 'file'
    owner = r'[A-Za-z0-9][A-Za-z0-9-]*'
    repository = r'[A-Za-z0-9_][A-Za-z0-9_.-]*'
    if re.fullmatch(r'https://github\.com/' + owner + '/' + repository, url):
        return 'https'
    if re.fullmatch(r'git@github\.com:' + owner + '/' + repository + r'\.git', url):
        return 'ssh'
    raise ReceiveError('受信元はGitHub URLまたは同じ模擬環境のremote.gitに限定しています')


def _environment():
    # Do not inherit GIT_*, askpass, proxy, tracing, or injected configuration.
    allowed = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SSH_AUTH_SOCK')
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({
        'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_CONFIG_SYSTEM': '/dev/null',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_ASKPASS': '/usr/bin/false',
        'SSH_ASKPASS': '/usr/bin/false',
        'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_OPTIONAL_LOCKS': '0',
        'GIT_SSH_VARIANT': 'ssh',
        'GIT_SSH_COMMAND': shlex.join([
            '/usr/bin/ssh', '-F', '/dev/null',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UpdateHostKeys=no', '-o', 'AddKeysToAgent=no',
            '-o', 'IdentityFile=none', '-o', 'ControlMaster=no',
            '-o', 'ControlPath=none', '-o', 'ConnectTimeout=15',
        ]),
    })
    return environment


class _Reader:
    def __init__(self, home, transport, home_guard=check_home):
        self.home = home
        self.cache = home / '.local/share/ai-knowledge/receive-cache.git'
        self.transport = transport
        self.home_guard = home_guard
        self.environment = _environment()
        self.git = shutil.which('git')
        if not self.git:
            raise ReceiveError('Gitが見つかりません')

    def command(self, *arguments, repository=True, allow_failure=False):
        command = [self.git, '--no-pager', '--no-replace-objects', '--literal-pathspecs']
        overrides = {
            'core.hooksPath': '/dev/null', 'core.askPass': '/usr/bin/false',
            'core.fsmonitor': 'false', 'credential.helper': '',
            'credential.interactive': 'false', 'protocol.allow': 'never',
            'protocol.' + self.transport + '.allow': 'always',
            'http.followRedirects': 'false', 'http.sslVerify': 'true',
            'http.proxy': '', 'fetch.recurseSubmodules': 'false',
            'fetch.fsckObjects': 'true', 'gc.auto': '0',
        }
        for key, value in overrides.items():
            command.extend(('-c', key + '=' + value))
        if repository:
            command.append('--git-dir=' + str(self.cache))
        command.extend(arguments)
        try:
            result = subprocess.run(
                command, cwd=self.home, env=self.environment, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=GIT_TIMEOUT, shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ReceiveError('Git受信または読込が時間内に完了しませんでした') from error
        except OSError as error:
            raise ReceiveError('Git受信または読込を開始できませんでした') from error
        if result.returncode and not allow_failure:
            raise ReceiveError('Git受信または読込に失敗しました。前の共有版を保持します')
        return result

    def prepare(self):
        if not self.cache.exists():
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.command('init', '--bare', '--template=', '--object-format=sha1',
                         str(self.cache), repository=False)
            (self.cache / 'config').write_text(_CACHE_CONFIG, encoding='utf-8')
        self.home_guard(self.home)
        config = self.cache / 'config'
        if (not self.cache.is_dir() or not config.is_file()
                or not stat.S_ISREG(config.stat().st_mode)
                or config.stat().st_size > 1024
                or config.read_text(encoding='utf-8') != _CACHE_CONFIG):
            raise ReceiveError('専用Gitキャッシュの設定が変更されています')
        for name in ('commondir', 'objects/info/alternates', 'info/grafts', 'shallow'):
            if (self.cache / name).exists():
                raise ReceiveError('専用Gitキャッシュに未対応の設定があります')

    def blob(self, commit, path, maximum):
        result = self.command('ls-tree', '-z', commit, '--', path)
        entries = result.stdout.split(b'\0')
        if len(entries) != 2 or entries[-1] != b'':
            raise ReceiveError('共有元の通常ファイルが見つかりません')
        metadata, separator, filename = entries[0].partition(b'\t')
        if not separator or filename != path.encode('utf-8'):
            raise ReceiveError('共有元のファイル情報が不正です')
        values = metadata.split(b' ')
        if (len(values) != 3 or values[0] not in (b'100644', b'100755')
                or values[1] != b'blob'):
            raise ReceiveError('共有元のリンク・サブモジュール・特殊ファイルは受信できません')
        oid = values[2].decode('ascii')
        if not _OID.fullmatch(oid):
            raise ReceiveError('共有元のGitオブジェクトが不正です')
        size_text = self.command('cat-file', '-s', oid).stdout
        try:
            size = int(size_text)
        except ValueError as error:
            raise ReceiveError('共有元のファイルサイズが不正です') from error
        if not 0 <= size <= maximum:
            raise ReceiveError('共有元のファイルがサイズ上限を超えています')
        raw = self.command('cat-file', 'blob', oid).stdout
        if len(raw) != size:
            raise ReceiveError('共有元のファイルサイズが一致しません')
        try:
            return raw.decode('utf-8'), size
        except UnicodeError as error:
            raise ReceiveError('共有元のファイルはUTF-8テキストに限定しています') from error


def validate_config(home: Path, config: dict, *, home_guard=check_home,
                    allow_local=True) -> None:
    """Validate a source using local checks only; do not create a cache or connect."""
    home = home_guard(Path(home))
    if not isinstance(config, dict) or set(config) != {'url', 'branch', 'manifest'}:
        raise ReceiveError('Git受信設定の形式が不正です')
    transport = _endpoint(home, config['url'], allow_local)
    branch = config['branch']
    if (not isinstance(branch, str) or not branch or branch.startswith('-')
            or any(ord(character) < 33 or ord(character) == 127 for character in branch)):
        raise ReceiveError('受信対象ブランチが不正です')
    _relative(config['manifest'])
    reader = _Reader(home, transport, home_guard)
    if reader.command('check-ref-format', 'refs/heads/' + branch,
                      repository=False, allow_failure=True).returncode:
        raise ReceiveError('受信対象ブランチが不正です')


def receive(home: Path, config: dict, previous: dict | None = None, *,
            home_guard=check_home, allow_local=True) -> dict:
    """Fetch a fixed source, reject rewritten history, and return raw shared content."""
    validate_config(home, config, home_guard=home_guard, allow_local=allow_local)
    home = Path(os.path.abspath(Path(home).expanduser()))
    transport = _endpoint(home, config['url'], allow_local)
    branch = config['branch']
    manifest_path = PurePosixPath(config['manifest'])
    source = {'kind': 'git', **config}
    if previous is not None:
        if (not isinstance(previous, dict)
                or any(previous.get(key) != value for key, value in source.items())
                or not isinstance(previous.get('commit'), str)
                or not _OID.fullmatch(previous['commit'])):
            raise ReceiveError('前回と受信元が異なります。自動で切り替えません')
    reader = _Reader(home, transport, home_guard)
    reader.prepare()
    reader.command('fetch', '--no-tags', '--no-recurse-submodules',
                   '--no-auto-maintenance', '--no-write-commit-graph',
                   '--no-write-fetch-head', '--atomic', '--', config['url'],
                   '+refs/heads/' + branch + ':refs/ai-knowledge/received')
    commit = reader.command('rev-parse', '--verify',
                            'refs/ai-knowledge/received^{commit}').stdout.decode('ascii').strip()
    if not _OID.fullmatch(commit):
        raise ReceiveError('受信したコミットが不正です')
    if previous:
        ancestry = reader.command('merge-base', '--is-ancestor', previous['commit'], commit,
                                  allow_failure=True)
        if ancestry.returncode == 1:
            raise ReceiveError('共有元の履歴変更を検出しました。前の共有版を保持します')
        if ancestry.returncode:
            raise ReceiveError('前回コミットとの関係を確認できません。前の共有版を保持します')
    manifest_text, total = reader.blob(commit, str(manifest_path), MAX_MANIFEST)
    try:
        manifest = json.loads(manifest_text)
    except (ValueError, UnicodeError) as error:
        raise ReceiveError('共有manifestのJSONが不正です') from error
    if (not isinstance(manifest, dict)
            or set(manifest) != {'schema', 'version', 'rules', 'files', 'hooks'}
            or type(manifest.get('schema')) is not int or manifest['schema'] != 1
            or not isinstance(manifest['version'], str)
            or not isinstance(manifest['files'], list)
            or not isinstance(manifest['hooks'], list)):
        raise ReceiveError('共有manifestの形式が不正です')
    rules = str(_relative(manifest['rules']))
    names = [str(_relative(name)) for name in manifest['files']]
    if len(names) != len(set(names)) or rules in names:
        raise ReceiveError('共有manifestのファイルが重複しています')
    received = {}
    for name in [rules, *names]:
        text, size = reader.blob(commit, str(manifest_path.parent / name), MAX_BLOB)
        total += size
        if total > MAX_TOTAL:
            raise ReceiveError('共有元の合計サイズが上限を超えています')
        received[name] = text
    return {
        'bundle': {'version': manifest['version'], 'rules': received[rules],
                   'files': {name: received[name] for name in names}, 'hooks': manifest['hooks']},
        'source': {**source, 'commit': commit},
    }
