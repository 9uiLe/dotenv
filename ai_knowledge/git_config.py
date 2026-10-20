"""Gitの共通設定だけを配布トランザクションへ組み込む。"""

import json
import os
import shlex
import subprocess

from prototype.engine import Conflict, read_file


CHECKER = 'hooks/token_limit.py'
CONFIG = '.gitconfig'
INCLUDE = '.local/share/ai-knowledge/git-config'
HOOKS = '.local/share/ai-knowledge/git-hooks/'
START = '# ai-knowledge:git-hooks:begin'
END = '# ai-knowledge:git-hooks:end'


def global_hooks_path(target):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('GIT_')}
    environment['HOME'] = str(target.home)
    if target.mode == 'mock':
        environment['XDG_CONFIG_HOME'] = str(target.home / '.config')
    result = subprocess.run(
        ['git', 'config', '--global', '--includes', '--get', 'core.hooksPath'],
        cwd=target.home, env=environment, capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 1:
        return None
    if result.returncode:
        raise Conflict('既存のGit共通設定を読み取れません')
    return result.stdout.removesuffix('\n')


def specs(target, bundle, state):
    if CHECKER not in bundle['files']:
        return {}
    from ai_knowledge.git_dispatch import HOOK_EVENTS

    if target.mode == 'live' and os.environ.get('GIT_CONFIG_GLOBAL'):
        raise Conflict('GIT_CONFIG_GLOBALで設定先が変更されています。標準のGit設定場所を確認してください')
    compile(bundle['files'][CHECKER], CHECKER, 'exec')
    old = (state.get('active') or {}).get('managed', {}).get(CONFIG)
    previous = old['previous_hooks_path'] if old else global_hooks_path(target)
    hook_directory = target.home / HOOKS
    if previous is not None and os.path.abspath(os.path.expanduser(previous)) == str(hook_directory):
        raise Conflict('引き継げないGit共通フック設定があります')
    block = ('\n\n' + START + '\n[include]\n\tpath = '
             + json.dumps(str(target.home / INCLUDE), ensure_ascii=False) + '\n' + END + '\n')
    result = {
        CONFIG: {'kind': 'git_config', 'payload': block, 'previous_hooks_path': previous},
        INCLUDE: {'kind': 'file', 'payload': '[core]\n\thooksPath = '
                  + json.dumps(str(hook_directory), ensure_ascii=False) + '\n'},
    }
    for event in (*HOOK_EVENTS, 'check-token-limit'):
        command = shlex.join([
            str(target.python), str(target.runtime / 'ai_knowledge/git_dispatch.py'),
            '--home', str(target.home), '--previous-hooks-path-json', json.dumps(previous),
            *(['--check-only', 'pre-commit'] if event == 'check-token-limit' else [event]),
        ])
        result[HOOKS + event] = {'kind': 'executable', 'payload': '#!/bin/sh\nexec ' + command + ' "$@"\n'}
    return result


def managed_setting(relative):
    from ai_knowledge.git_dispatch import HOOK_EVENTS
    return relative in {CONFIG, INCLUDE, *(HOOKS + event for event in (*HOOK_EVENTS, 'check-token-limit'))}


def merge_config(path, new, old, original):
    before = read_file(path)
    current = before['text'] if before else ''
    if old:
        if current.count(old['payload']) != 1 or current.count(START) != 1:
            raise Conflict('Git共通フックの登録が直接編集されています')
        result = current.replace(old['payload'], new['payload'] if new else '', 1)
    else:
        if START in current or END in current:
            raise Conflict('引き継げないGit共通フックの登録があります')
        result = current + new['payload']
    if not new and original is None and not result:
        return before, None
    return before, {'text': result, 'mode': before['mode'] if before else 0o644}
