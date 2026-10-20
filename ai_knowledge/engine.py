"""共有設定の状態管理・差分作成・トランザクションと復元を扱う。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

from ai_knowledge.engine_settings import SettingsMixin
from ai_knowledge.engine_support import (
    Conflict, END, REPO, RULES, SCHEDULE, SKILLS, START, TOML_END, TOML_START,
    checksum, encoded, format_diff, read_file, validate_bundle, write_atomic,
)
from ai_knowledge.sandbox import check_home


class Manager(SettingsMixin):
    def __init__(self, home, source, profile='personal'):
        home = check_home(Path(home))
        if profile not in {'personal', 'work'} or home.name != profile:
            raise ValueError('模擬PCとプロファイルが一致しません')
        source = Path(source).absolute()
        if source != home.parent / 'source':
            raise ValueError('共有元は同じ模擬環境のsourceに限定しています')
        self._initialize(home, source, profile, check_home)

    def _initialize(self, home, source, profile, home_guard):
        """Share transactions with an explicitly guarded installed runtime."""
        self.home = home
        self.root = home.parent
        self.profile = profile
        self.source = source
        self._home_guard = home_guard
        self.tools = ('codex',) if profile == 'personal' else ('codex', 'claude')
        self.data = self.home / '.local/share/ai-knowledge'
        self.state_path = self.data / 'state.json'
        self.lock_path = self.data / 'operation.lock'

    def _default(self):
        return {'schema': 1, 'phase': 'ready', 'sync_enabled': True, 'hooks_enabled': True,
                'active': None, 'history': [], 'original': {}, 'last_success_date': None,
                'last_success_at': None, 'last_received_source': None, 'last_error': None}

    def _state_text(self):
        return self.state_path.read_text(encoding='utf-8')

    def _state(self):
        state = json.loads(self._state_text()) if self.state_path.exists() else self._default()
        if (not isinstance(state, dict) or state.get('schema') != 1
                or state.get('phase') not in {'ready', 'applying', 'recovery_required', 'uninstalled'}
                or type(state.get('sync_enabled')) is not bool or type(state.get('hooks_enabled')) is not bool):
            raise Conflict('管理状態が不正です。書き込みを止めました')
        return state

    def _save(self, state):
        write_atomic(self.state_path, encoded(state))

    @contextmanager
    def _lock(self):
        self._home_guard(self.home)
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
        self._home_guard(self.home)
        return deepcopy(self._state())

    def _path(self, relative):
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or str(path) != relative:
            raise Conflict('管理対象のパスが不正です')
        return self.home / relative

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
                self._home_guard(self.home)
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
            from ai_knowledge.git_source import receive
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
