"""共有設定の定数・入力検証・ファイル入出力をまとめる。"""

import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile


REPO = Path(__file__).resolve().parents[1]
START = '<!-- ai-knowledge:begin -->'
END = '<!-- ai-knowledge:end -->'
TOML_START = '# ai-knowledge:begin'
TOML_END = '# ai-knowledge:end'
SCHEDULE = 'Library/LaunchAgents/local.ai-knowledge.sync.plist'
RULES = {'codex': '.codex/AGENTS.md', 'claude': '.claude/CLAUDE.md'}
SKILLS = {'codex': '.agents/skills/', 'claude': '.claude/skills/'}


class Conflict(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def checksum(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def format_diff(before, after, before_name, after_name):
    result = []
    for line in difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                     fromfile=before_name, tofile=after_name):
        result.append(line if line.endswith('\n') else line + '\n')
        if not line.endswith('\n'):
            result.append('\\ No newline at end of file\n')
    return ''.join(result)


def write_atomic(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def read_file(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise Conflict(f'通常のファイルではありません: {path.name}')
    return {'text': path.read_text(encoding='utf-8'), 'mode': stat.S_IMODE(path.stat().st_mode)}


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'version', 'rules', 'files', 'hooks'}:
        raise Conflict('共有元の形式が不正です')
    if not isinstance(bundle['version'], str) or not re.fullmatch(r'[A-Za-z0-9._-]+', bundle['version']):
        raise Conflict('共有版の名前が不正です')
    if not isinstance(bundle['rules'], str) or START in bundle['rules'] or END in bundle['rules']:
        raise Conflict('共通ルールの形式が不正です')
    if not isinstance(bundle['files'], dict) or not isinstance(bundle['hooks'], list):
        raise Conflict('共有ファイルまたはHookの形式が不正です')
    for name, text in bundle['files'].items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise Conflict('共有ファイルはテキストだけを扱います')
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name
                or len(path.parts) < 2 or path.parts[0] not in {'skills', 'hooks'}):
            raise Conflict('共有ファイルが許可された範囲の外です')
        if path.parts[0] == 'skills' and (len(path.parts) < 3 or not path.parts[1].startswith('ai-knowledge-')):
            raise Conflict('共有スキル名はai-knowledge-で始めてください')
    identifiers = set()
    for hook in bundle['hooks']:
        if not isinstance(hook, dict) or set(hook) != {'id', 'event', 'script', 'tools', 'timeout'}:
            raise Conflict('Hook定義の形式が不正です')
        if not isinstance(hook['id'], str) or not re.fullmatch(r'[a-z0-9-]+', hook['id']) or hook['id'] in identifiers:
            raise Conflict('Hookの名前が不正または重複しています')
        identifiers.add(hook['id'])
        if not isinstance(hook['event'], str) or not re.fullmatch(r'[A-Z][A-Za-z]+', hook['event']):
            raise Conflict('Hookイベント名が不正です')
        if (not isinstance(hook['script'], str) or not hook['script'].startswith('hooks/')
                or not hook['script'].endswith('.py') or hook['script'] not in bundle['files']):
            raise Conflict('HookのPythonスクリプトが見つかりません')
        if (not isinstance(hook['tools'], list) or not hook['tools']
                or any(tool not in {'codex', 'claude'} for tool in hook['tools'])):
            raise Conflict('Hookの対象ツールが不正です')
        if type(hook['timeout']) is not int or not 1 <= hook['timeout'] <= 60:
            raise Conflict('Hookのタイムアウトは1〜60秒です')
        compile(bundle['files'][hook['script']], hook['script'], 'exec')
    return bundle
