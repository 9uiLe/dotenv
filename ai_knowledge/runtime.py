"""導入先の境界と共有元検証を適用する配布エンジン。"""

from copy import deepcopy
import json
import os
import plistlib
import re
import shlex
import stat

from ai_knowledge.target import Target
from ai_knowledge.engine import Conflict, Manager, RULES, SKILLS, checksum, encoded, read_file, validate_bundle
from ai_knowledge.git_source import receive, validate_config
from ai_knowledge.hook_runner import run as run_shared_hook


MAX_STATE_BYTES = 64 * 1024 * 1024


class LiveManager(Manager):
    def __init__(self, target: Target):
        if not isinstance(target, Target):
            raise ValueError('導入先の明示指定が必要です')
        self.target = target
        target.guard()
        self._initialize(target.home, target.source, target.profile, target.guard)

    def _path(self, relative):
        return self.target.path(relative)

    def _managed_setting(self, relative):
        from ai_knowledge.git_config import managed_setting
        if managed_setting(relative):
            return True
        if relative in {RULES[tool] for tool in self.tools}:
            return True
        if relative in {'.codex/config.toml', '.codex/hooks.json'}:
            return True
        if self.profile == 'work' and relative == '.claude/settings.json':
            return True
        return any(relative.startswith(SKILLS[tool]) for tool in self.tools)

    def _state_text(self):
        descriptor = os.open(self.state_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise Conflict('管理状態が通常ファイルではありません')
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise Conflict('管理状態が64MiBを超えました。旧状態を保持し、履歴の手動整理が必要です')
        return raw.decode('utf-8')

    def _state(self):
        state = super()._state()
        if not isinstance(state.get('original'), dict) or not isinstance(state.get('history'), list):
            raise Conflict('管理状態の保存内容が不正です')
        for relative in state['original']:
            self._path(relative)
        for active in [state.get('active'), *state['history']]:
            if active is None:
                continue
            if not isinstance(active, dict) or not isinstance(active.get('managed'), dict):
                raise Conflict('配布履歴が不正です')
            bundle = validate_bundle(deepcopy(active.get('bundle')))
            revision = active.get('revision')
            if (not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision)
                    or revision != checksum(encoded(bundle))):
                raise Conflict('配布履歴の版が不正です')
            for relative in active['managed']:
                self._path(relative)
                if not self._managed_setting(relative):
                    raise Conflict('配布履歴に設定対象外のパスがあります')
        return state

    def _transaction(self, state, next_state, plans, bundle=None, fail_after=None):
        applying = {**state, 'phase': 'applying'}
        if any(len(encoded(candidate).encode('utf-8')) > MAX_STATE_BYTES
               for candidate in (applying, next_state)):
            raise Conflict('更新後の管理状態が64MiBを超えます。旧状態を保持し、履歴の手動整理が必要です')
        return super()._transaction(state, next_state, plans, bundle, fail_after)

    def _hook_entries(self, bundle, tool):
        entries = []
        for hook in bundle['hooks']:
            if tool in hook['tools']:
                command = shlex.join([
                    str(self.target.python), str(self.target.runtime / 'ai_knowledge/entrypoint.py'),
                    'hook', '--tool', tool, '--hook', hook['id'],
                ])
                entries.append({'event': hook['event'], 'group': {'hooks': [
                    {'type': 'command', 'command': command, 'timeout': hook['timeout']}]}})
        return entries

    def _schedule(self):
        return plistlib.dumps({
            'Label': 'local.ai-knowledge.sync', 'RunAtLoad': True,
            'StartCalendarInterval': {'Hour': 10, 'Minute': 0},
            'ProgramArguments': [str(self.target.python),
                                 str(self.target.runtime / 'ai_knowledge/entrypoint.py'), 'sync'],
            'WorkingDirectory': str(self.target.home),
            'StandardOutPath': str(self.data / 'sync.log'),
            'StandardErrorPath': str(self.data / 'sync-error.log'),
        }).decode()

    def _specs(self, bundle, state):
        override = read_file(self._path('.codex/AGENTS.override.md'))
        if override and override['text'].strip():
            raise Conflict('AGENTS.override.mdがあるため共有指示の優先順位を確認してください')
        # Validate only the named managed skill trees and their parent components.
        for tool in self.tools:
            for name in bundle['files']:
                if name.startswith('skills/'):
                    self._path(SKILLS[tool] + name.removeprefix('skills/'))
        from ai_knowledge.git_config import specs
        return {**super()._specs(bundle, state), **specs(self.target, bundle, state)}

    def _merge(self, relative, new, old, original):
        from ai_knowledge.git_config import merge_config
        kind = (old or new)['kind']
        if kind == 'git_config':
            return merge_config(self._path(relative), new, old, original)
        if kind == 'executable':
            # 同一ファイルの衝突・直接編集の検出は通常ファイルと共通にする。
            as_file = lambda spec: {**spec, 'kind': 'file'} if spec else None
            before, after = super()._merge(relative, as_file(new), as_file(old), original)
            if before is not None and old and before['mode'] != 0o755:
                raise Conflict('Git共通フックの実行権限が直接編集されています')
            if after is not None:
                after['mode'] = 0o755
            return before, after
        return super()._merge(relative, new, old, original)

    def _receive(self, state):
        configuration = read_file(self.source / 'git.json')
        if configuration is None:
            raise Conflict('Gitの受信設定が見つかりません')
        config = json.loads(configuration['text'])
        self.validate_source(config)
        previous = state.get('last_received_source') or (state['active'] or {}).get('source')
        received = receive(self.home, config, previous=previous,
                           home_guard=self.target.guard, allow_local=self.target.mode == 'mock')
        if self.target.mode == 'live':
            from ai_knowledge.source import require_ci_success
            require_ci_success(received['source'])
        return validate_bundle(received['bundle']), received['source']

    def validate_source(self, config):
        if self.target.mode == 'live' and (not isinstance(config, dict)
                or not isinstance(config.get('url'), str)
                or not config['url'].startswith('https://github.com/')):
            raise Conflict('本番の受信元は公開GitHubのHTTPS URLに限定しています')
        validate_config(self.home, config, home_guard=self.target.guard,
                        allow_local=self.target.mode == 'mock')


def run_hook(target: Target, tool: str, hook_id: str) -> int:
    target.guard()
    if tool not in target.tools:
        raise ValueError('この導入先では有効でないツールです')
    return run_shared_hook(target.home, tool, hook_id, home_guard=target.guard,
                           max_state_bytes=MAX_STATE_BYTES)
