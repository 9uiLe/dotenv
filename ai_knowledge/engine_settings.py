"""各ツールの共有設定と日次更新設定の構築・マージを扱う。"""

import json
from pathlib import PurePosixPath
import plistlib
import re
import shlex
import sys
import tomllib

from ai_knowledge.engine_support import (
    Conflict, END, REPO, RULES, SCHEDULE, SKILLS, START, TOML_END, TOML_START,
    encoded, read_file,
)


class SettingsMixin:
    def _hook_entries(self, bundle, tool):
        entries = []
        for hook in bundle['hooks']:
            if tool in hook['tools']:
                command = shlex.join([sys.executable, str(REPO / 'ai_knowledge/hook_runner.py'),
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
                hooks = tomllib.loads(config['text']).get('hooks', {}) if config is not None else {}
                inline = isinstance(hooks, dict) and any(
                    re.fullmatch(r'[A-Z][A-Za-z]+', event) and isinstance(groups, list)
                    for event, groups in hooks.items())
                # Codex adds hooks.state trust metadata even when handlers live in JSON.
                # Keep the representation recorded at installation across later updates.
                if '.codex/hooks.json' in old:
                    inline = False
                elif '.codex/config.toml' in old:
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
                               'ProgramArguments': [sys.executable, '-m', 'ai_knowledge', '--sandbox', str(self.root),
                                                    '--profile', self.profile, 'sync']}).decode()

    def _schedule_plan(self, state, enabled):
        current = read_file(self.home / SCHEDULE)
        expected = {'text': self._schedule(), 'mode': 0o644}
        if current is not None and (SCHEDULE not in state['original'] or current != expected):
            raise Conflict('日次更新の設定が既に存在するか、直接編集されています')
        return current, expected if enabled else None
