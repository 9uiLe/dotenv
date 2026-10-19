"""Persistent distribution engine restricted to marked, repository-local mock homes."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shlex
import stat
import sys
import tempfile
import tomllib

from prototype.sandbox import check_home


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


class Manager:
    def __init__(self, home, source, profile='personal'):
        self.home = check_home(Path(home))
        if profile not in {'personal', 'work'} or self.home.name != profile:
            raise ValueError('模擬PCとプロファイルが一致しません')
        self.profile = profile
        self.root = self.home.parent
        self.source = Path(source).absolute()
        if self.source != self.root / 'source':
            raise ValueError('共有元は同じ模擬環境のsourceに限定しています')
        self.tools = ('codex',) if profile == 'personal' else ('codex', 'claude')
        self.data = self.home / '.local/share/ai-knowledge'
        self.state_path = self.data / 'state.json'
        self.lock_path = self.data / 'operation.lock'

    def _default(self):
        return {'schema': 1, 'phase': 'ready', 'sync_enabled': True, 'hooks_enabled': True,
                'active': None, 'history': [], 'original': {}, 'last_success_date': None,
                'last_success_at': None, 'last_received_source': None, 'last_error': None}

    def _state(self):
        state = json.loads(self.state_path.read_text()) if self.state_path.exists() else self._default()
        if (not isinstance(state, dict) or state.get('schema') != 1
                or state.get('phase') not in {'ready', 'applying', 'recovery_required', 'uninstalled'}
                or type(state.get('sync_enabled')) is not bool or type(state.get('hooks_enabled')) is not bool):
            raise Conflict('管理状態が不正です。書き込みを止めました')
        return state

    def _save(self, state):
        write_atomic(self.state_path, encoded(state))

    @contextmanager
    def _lock(self):
        check_home(self.home)
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data, 0o700)
        with self.lock_path.open('a+') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise Conflict('同期・復元・共有Hookが実行中です') from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def status(self):
        check_home(self.home)
        return deepcopy(self._state())

    def _path(self, relative):
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or str(path) != relative:
            raise Conflict('管理対象のパスが不正です')
        return self.home / relative

    def _hook_entries(self, bundle, tool):
        entries = []
        for hook in bundle['hooks']:
            if tool in hook['tools']:
                command = shlex.join([sys.executable, str(REPO / 'prototype/hook_runner.py'),
                                      '--home', str(self.home), '--tool', tool, '--hook', hook['id']])
                entries.append({'event': hook['event'], 'group': {'hooks': [
                    {'type': 'command', 'command': command, 'timeout': hook['timeout']}]}})
        return entries

    def _specs(self, bundle, state):
        specs = {}
        old = state['active']['managed'] if state['active'] else {}
        for tool in self.tools:
            specs[RULES[tool]] = {'kind': 'block', 'payload': '\n\n' + START + '\n' + bundle['rules'].rstrip() + '\n' + END + '\n'}
            for name, text in bundle['files'].items():
                if name.startswith('skills/'):
                    relative = SKILLS[tool] + name.removeprefix('skills/')
                    if relative not in old:
                        skill_root = self.home / SKILLS[tool] / PurePosixPath(name).parts[1]
                        if skill_root.exists() and (state['phase'] != 'uninstalled' or any(skill_root.iterdir())):
                            raise Conflict('同じ名前のスキルフォルダが既にあります')
                    specs[relative] = {'kind': 'file', 'payload': text}
            entries = self._hook_entries(bundle, tool)
            if tool == 'codex':
                config = read_file(self.home / '.codex/config.toml')
                inline = config is not None and 'hooks' in tomllib.loads(config['text'])
                if '.codex/config.toml' in old:
                    inline = True
                if inline:
                    lines = ['\n\n' + TOML_START]
                    for entry in entries:
                        lines.extend([f"[[hooks.{entry['event']}]]", f"[[hooks.{entry['event']}.hooks]]"])
                        lines.extend(f'{key} = {json.dumps(value)}' for key, value in entry['group']['hooks'][0].items())
                    specs['.codex/config.toml'] = {'kind': 'toml_block', 'payload': '\n'.join(lines) + '\n' + TOML_END + '\n'}
                else:
                    specs['.codex/hooks.json'] = {'kind': 'json_hooks', 'payload': entries}
            else:
                specs['.claude/settings.json'] = {'kind': 'json_hooks', 'payload': entries}
        return specs

    def _merge(self, relative, new, old, original):
        before = read_file(self._path(relative))
        current = before['text'] if before else ''
        spec = old or new
        kind = spec['kind']
        if old and new and kind != new['kind']:
            raise Conflict('管理形式が変わりました')
        if kind in {'block', 'toml_block'}:
            marker = START if kind == 'block' else TOML_START
            if old:
                if current.count(old['payload']) != 1 or current.count(marker) != 1:
                    raise Conflict(f'共有部分が直接編集されています: {relative}')
                result = current.replace(old['payload'], new['payload'] if new else '', 1)
            else:
                if marker in current:
                    raise Conflict(f'引き継げない共有部分があります: {relative}')
                result = current + new['payload']
            if kind == 'toml_block':
                tomllib.loads(result)
        elif kind == 'file':
            if old:
                if before is None or current != old['payload']:
                    raise Conflict(f'共有ファイルが直接編集されています: {relative}')
            elif before is not None:
                raise Conflict(f'同じ名前のファイルが既にあります: {relative}')
            result = new['payload'] if new else None
        else:
            data = json.loads(current) if current else {}
            if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
                raise Conflict('既存のHook設定形式が不正です')
            hooks = data.setdefault('hooks', {})
            for entry in old['payload'] if old else []:
                groups = hooks.get(entry['event'], [])
                if not isinstance(groups, list) or groups.count(entry['group']) != 1:
                    raise Conflict('共有Hookの登録が直接編集されています')
                groups.remove(entry['group'])
                if not groups:
                    hooks.pop(entry['event'], None)
            for entry in new['payload'] if new else []:
                groups = hooks.setdefault(entry['event'], [])
                if not isinstance(groups, list) or entry['group'] in groups:
                    raise Conflict('共有Hookと同じ登録または不正な設定があります')
                groups.append(entry['group'])
            initial = json.loads(original['text']) if original and original['text'] else {}
            if not hooks and 'hooks' not in initial:
                data.pop('hooks')
            result = encoded(data)
        return before, None if result is None else {'text': result, 'mode': before['mode'] if before else 0o644}

    def _schedule(self):
        return plistlib.dumps({'Label': 'local.ai-knowledge.sync', 'RunAtLoad': True,
                               'StartCalendarInterval': {'Hour': 10, 'Minute': 0},
                               'WorkingDirectory': str(REPO),
                               'ProgramArguments': [sys.executable, '-m', 'prototype', '--sandbox', str(self.root),
                                                    '--pc', self.profile, 'sync']}).decode()

    def _schedule_plan(self, state, enabled):
        current = read_file(self.home / SCHEDULE)
        expected = {'text': self._schedule(), 'mode': 0o644}
        if current is not None and (SCHEDULE not in state['original'] or current != expected):
            raise Conflict('日次更新の設定が既に存在するか、直接編集されています')
        return current, expected if enabled else None

    def _plan(self, bundle, state, allow_removal=False):
        if state['active']:
            active = state['active']
            for name, text in active['bundle']['files'].items():
                saved = self.data / 'releases' / active['revision'] / name
                if not saved.is_file() or saved.read_text(encoding='utf-8') != text:
                    raise Conflict('配布済みの共有版が直接編集されています')
        old = state['active']['managed'] if state['active'] else {}
        new = self._specs(bundle, state) if bundle else {}
        if not allow_removal and state['active']:
            previous = state['active']['bundle']
            if (set(previous['files']) - set(bundle['files']) or set(old) - set(new)
                    or {h['id'] for h in previous['hooks']} - {h['id'] for h in bundle['hooks']}):
                raise Conflict('共有元から管理対象が消えました。自動削除は行いません')
        plans = {}
        for relative in sorted(old.keys() | new.keys()):
            plans[relative] = self._merge(relative, new.get(relative), old.get(relative), state['original'].get(relative))
        return new, plans

    def _changes(self, plans):
        return [{'path': str(self._path(name)), 'action': 'retire' if after is None else 'write',
                 'diff': format_diff((before or {}).get('text', ''), (after or {}).get('text', ''), name, name)}
                for name, (before, after) in sorted(plans.items()) if before != after]

    def _retire(self, path):
        folder = Path(tempfile.mkdtemp(prefix='retired-', dir=self._private_dir('retired')))
        os.replace(path, folder / path.name)

    def _private_dir(self, name):
        path = self.data / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        return path

    def _restore_files(self, plans):
        for name, (before, after) in plans.items():
            path = self._path(name)
            current = read_file(path)
            if current not in (before, after):
                raise Conflict('復旧中に個別の変更を検出しました')
            if current != before:
                if before is None:
                    if current is not None:
                        self._retire(path)
                else:
                    write_atomic(path, before['text'], before['mode'])

    def _recover(self, state):
        if state['phase'] == 'applying':
            journal = json.loads((self.data / 'pending.json').read_text())
            try:
                self._restore_files(journal['plans'])
                state = journal['before']
                state['last_error'] = '中断された更新を旧状態へ復旧しました'
            except Exception:
                state['phase'] = 'recovery_required'
                state['sync_enabled'] = state['hooks_enabled'] = False
                self._save(state)
                raise Conflict('自動復旧できませんでした。更新と共有Hookを停止しました')
            self._save(state)
        if state['phase'] == 'recovery_required':
            raise Conflict('復旧が必要なため更新と共有Hookを停止しています')
        return state

    def _transaction(self, state, next_state, plans, bundle=None, fail_after=None):
        changed = {name: pair for name, pair in plans.items() if pair[0] != pair[1]}
        folder = Path(tempfile.mkdtemp(prefix='backup-', dir=self._private_dir('backups')))
        write_atomic(folder / 'before.json', encoded({'state': state, 'files': plans}))
        write_atomic(self.data / 'pending.json', encoded({'before': state, 'plans': plans}))
        applying = deepcopy(state)
        applying['phase'] = 'applying'
        self._save(applying)
        try:
            if bundle:
                revision = next_state['active']['revision']
                release = self._private_dir('releases') / revision
                for name, text in bundle['files'].items():
                    path = release / name
                    if path.exists() and path.read_text() != text:
                        raise Conflict('保存済みの共有版が直接編集されています')
                    if not path.exists():
                        write_atomic(path, text, 0o644)
            for index, (name, (before, after)) in enumerate(changed.items(), 1):
                check_home(self.home)
                path = self._path(name)
                if read_file(path) != before:
                    raise Conflict('適用直前に設定が変わりました')
                if after is None:
                    self._retire(path)
                else:
                    write_atomic(path, after['text'], after['mode'])
                if fail_after == index:
                    raise OSError('模擬的な書き込み障害')
            self._save(next_state)
        except Exception as error:
            try:
                self._restore_files(plans)
                state['last_error'] = str(error)
                self._save(state)
            except Exception:
                applying['phase'] = 'recovery_required'
                applying['sync_enabled'] = applying['hooks_enabled'] = False
                applying['last_error'] = '旧状態へ復旧できませんでした'
                self._save(applying)
            raise

    def _failure(self, error):
        try:
            with self._lock():
                state = self._state()
                if state['phase'] != 'applying':
                    state['last_error'] = str(error)
                    self._save(state)
        except Exception:
            pass
        return {'outcome': 'failed', 'reason': str(error)}

    def _receive(self, state):
        configuration = read_file(self.source / 'git.json')
        previous = state.get('last_received_source') or (state['active'] or {}).get('source')
        if configuration is not None:
            from prototype.git_source import receive
            received = receive(self.home, json.loads(configuration['text']),
                               previous=previous if previous and previous.get('kind') == 'git' else None)
            return validate_bundle(received['bundle']), received['source']
        if previous and previous.get('kind') == 'git':
            raise Conflict('Gitの受信設定が見つかりません。ローカル共有元へ切り替えず停止しました')
        received = read_file(self.source / 'bundle.json')
        if received is None:
            raise Conflict('共有元を受信できませんでした')
        return validate_bundle(json.loads(received['text'])), {'kind': 'local'}

    def preview_sync(self, now=None):
        """Receive a candidate and show target changes; keep active state unchanged."""
        try:
            with self._lock():
                state = self._state()
                if state['phase'] in {'applying', 'recovery_required'}:
                    raise Conflict('中断された処理の復旧が必要です。プレビューでは変更しません')
                bundle, source = self._receive(state)
                _, plans = self._plan(bundle, state)
                plans[SCHEDULE] = self._schedule_plan(state, state['sync_enabled'])
                return {'outcome': 'preview', 'version': bundle['version'], 'source': source,
                        'bundle': bundle, 'changes': self._changes(plans)}
        except Exception as error:
            return {'outcome': 'failed', 'reason': str(error)}

    def sync(self, now=None, force=False, fail_after=None):
        now = now or datetime.now().astimezone()
        try:
            with self._lock():
                state = self._recover(self._state())
                if not state['sync_enabled']:
                    return {'outcome': 'paused'}
                if not force:
                    if state['last_success_date'] == now.date().isoformat():
                        return {'outcome': 'already_today'}
                    if now.hour < 10:
                        return {'outcome': 'not_due'}
                bundle, source = self._receive(state)
                managed, plans = self._plan(bundle, state)
                revision = checksum(encoded(bundle))
                next_state = deepcopy(state)
                if state['active'] and state['active']['revision'] != revision:
                    next_state['history'].append(state['active'])
                next_state['active'] = {'bundle': bundle, 'revision': revision, 'managed': managed,
                                        'source': source}
                next_state['last_received_source'] = source
                next_state['phase'] = 'ready'
                next_state['last_error'] = None
                next_state['last_success_date'] = now.date().isoformat()
                next_state['last_success_at'] = now.isoformat()
                if state['sync_enabled']:
                    plans[SCHEDULE] = self._schedule_plan(state, True)
                for name, (before, _) in plans.items():
                    next_state['original'].setdefault(name, before)
                self._transaction(state, next_state, plans, bundle, fail_after)
                return {'outcome': 'updated' if self._changes(plans) or not state['active']
                        or state['active']['revision'] != revision else 'unchanged',
                        'version': bundle['version'], 'source': source, 'changes': self._changes(plans)}
        except Exception as error:
            return self._failure(error)

    def _control(self, key, enabled):
        try:
            with self._lock():
                state = self._recover(self._state())
                next_state = deepcopy(state)
                next_state[key] = enabled
                plans = {}
                if key == 'sync_enabled':
                    plans[SCHEDULE] = self._schedule_plan(state, enabled)
                    next_state['original'].setdefault(SCHEDULE, plans[SCHEDULE][0])
                self._transaction(state, next_state, plans)
                return {'outcome': 'changed', key: enabled}
        except Exception as error:
            return {'outcome': 'failed', 'reason': str(error)}

    def pause_sync(self):
        return self._control('sync_enabled', False)

    def resume_sync(self):
        return self._control('sync_enabled', True)

    def pause_hooks(self):
        return self._control('hooks_enabled', False)

    def resume_hooks(self):
        return self._control('hooks_enabled', True)

    def _undo(self, uninstall, apply):
        try:
            with self._lock():
                state = self._state()
                if not apply and state['phase'] in {'applying', 'recovery_required'}:
                    raise Conflict('中断された処理の復旧が必要です。プレビューでは変更しません')
                if apply:
                    state = self._recover(state)
                if not state['active']:
                    raise Conflict('配布済みの共有版がありません')
                if not uninstall and not state['history']:
                    raise Conflict('前の共有版がありません')
                target = None if uninstall else state['history'][-1]['bundle']
                managed, plans = self._plan(target, state, allow_removal=True)
                plans[SCHEDULE] = self._schedule_plan(state, False)
                changes = self._changes(plans)
                if not apply:
                    return {'outcome': 'preview', 'changes': changes}
                next_state = deepcopy(state)
                next_state['sync_enabled'] = next_state['hooks_enabled'] = False
                next_state['last_error'] = None
                if uninstall:
                    next_state['phase'] = 'uninstalled'
                    next_state['active'] = None
                    next_state['original'] = {}
                else:
                    next_state['history'] = state['history'][:-1] + [state['active']]
                    next_state['active'] = {'bundle': target, 'revision': checksum(encoded(target)), 'managed': managed,
                                            'source': state['history'][-1].get('source', {'kind': 'local'})}
                    next_state['phase'] = 'ready'
                self._transaction(state, next_state, plans, target)
                return {'outcome': 'uninstalled' if uninstall else 'restored', 'changes': changes}
        except Exception as error:
            return {'outcome': 'failed', 'reason': str(error)}

    def restore(self, previous=True, apply=False):
        if not previous:
            return {'outcome': 'failed', 'reason': '前の共有版への復元だけを扱います'}
        return self._undo(False, apply)

    def uninstall(self, apply=False):
        return self._undo(True, apply)
