"""導入済みランタイムだけを、確認した差分どおりに更新する。"""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from ai_knowledge.target import Target
from prototype.engine import checksum, encoded, format_diff, read_file, write_atomic
from prototype.sandbox import create_sandbox


DATA = '.local/share/ai-knowledge/'
INSTALLATION = DATA + 'installation.json'
STATE = DATA + 'state.json'


def _runtime_name(name):
    if not isinstance(name, str) or '\\' in name:
        return False
    path = PurePosixPath(name)
    return (len(path.parts) == 2 and path.parts[0] in {'ai_knowledge', 'prototype'}
            and str(path) == name and path.suffix == '.py'
            and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.py', path.name) is not None)


@contextmanager
def _lock(target):
    """既存の更新・共有Hookと同じロックを使い、対象には新規作成しない。"""
    target.guard()
    path = target.path(DATA + 'operation.lock')
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('同期・復元・共有Hookが実行中です') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _prepare(target, files):
    installation = read_file(target.path(INSTALLATION))
    if installation is None:
        raise ValueError('先に初回導入を完了してください')
    document = json.loads(installation['text'])
    identity = {'home': str(target.home), 'profile': target.profile,
                'mode': target.mode, 'python': str(target.python)}
    if (not isinstance(document, dict) or document.get('schema') != 1
            or any(document.get(key) != value for key, value in identity.items())):
        raise ValueError('導入情報と対象Mac・プロファイル・Pythonが一致しません')
    hashes = document.get('runtime_hashes')
    if (not isinstance(hashes, dict) or not hashes
            or any(not _runtime_name(name) or not isinstance(digest, str)
                   or re.fullmatch('[0-9a-f]{64}', digest) is None
                   for name, digest in hashes.items())):
        raise ValueError('導入済みランタイムの照合情報が不正です')
    before = {}
    for name, digest in hashes.items():
        relative = DATA + 'runtime/' + name
        saved = read_file(target.path(relative))
        if saved is None or checksum(saved['text']) != digest:
            raise ValueError('導入済みの実行処理が変更されています。更新せず確認してください')
        before[relative] = saved
    state = read_file(target.path(STATE))
    if state is None:
        raise ValueError('管理状態が見つかりません')
    status = json.loads(state['text'])
    if not isinstance(status, dict) or status.get('phase') not in {'ready', 'uninstalled'}:
        raise ValueError('中断された処理の復旧を先に完了してください')
    plans = {name: (saved, saved) for name, saved in before.items()}
    for name, text in files.items():
        relative = DATA + 'runtime/' + name
        saved = before.get(relative)
        if saved is None and read_file(target.path(relative)) is not None:
            raise ValueError('新しいランタイムの配置先に管理外のファイルがあります')
        plans[relative] = (saved, {'text': text, 'mode': saved['mode'] if saved else 0o600})
    updated = deepcopy(document)
    updated['runtime_hashes'] = {name: checksum(text) for name, text in files.items()}
    plans[INSTALLATION] = (installation, {'text': encoded(updated), 'mode': installation['mode']})
    return state, plans


def _capsule(target, files):
    state, plans = _prepare(target, files)
    return {'schema': 1, 'action': 'runtime-upgrade', 'home': str(target.home),
            'profile': target.profile, 'mode': target.mode, 'python': str(target.python),
            'state': state, 'plans': plans}


def create_upgrade_plan(target):
    """共有設定と導入先を変更せず、実行処理更新の差分を保存する。"""
    from ai_knowledge.installer import runtime_files
    files = runtime_files()
    with _lock(target):
        capsule = _capsule(target, files)
    folder = create_sandbox(prefix='runtime-upgrade-plan-')
    os.chmod(folder, 0o700)
    plan_path, patch_path, report_path = (folder / name for name in ('plan.json', 'changes.patch', 'report.md'))
    changes = []
    for name, (before, after) in capsule['plans'].items():
        if before != after:
            changes.append({'path': str(target.path(name)), 'action': 'write',
                            'diff': format_diff((before or {}).get('text', ''), after['text'], name, name)})
    write_atomic(plan_path, encoded(capsule))
    write_atomic(patch_path, ''.join(change['diff'] for change in changes))
    lines = ['# 導入済みランタイムの更新差分（未適用）', '',
             f'対象: `{target.home}` / `{target.profile}` / `{target.mode}`', '',
             '共有設定・管理状態・受信元・日次登録を保持し、実行処理と照合情報だけ更新します。', '',
             '[変更差分](changes.patch) / [適用時の照合データ](plan.json)', '',
             '適用前に導入情報・実行処理・管理状態を再照合し、変更があれば停止します。', '',
             '同期や共有Hookの実行中は適用できません。失敗時は書き込んだ実行処理を元へ戻します。', '',
             '## 変更対象', '']
    lines += ['- `' + change['path'] + '`' for change in changes]
    write_atomic(report_path, '\n'.join(lines) + '\n')
    return {'outcome': 'preview', 'action': 'runtime-upgrade', 'plan': str(plan_path),
            'patch': str(patch_path), 'report': str(report_path),
            'changes': [{key: change[key] for key in ('path', 'action')} for change in changes],
            'live_settings_changed': False}


def apply_upgrade_plan(plan_path):
    """レビュー後に変わっていない更新差分だけを排他的に適用する。"""
    from ai_knowledge.installer import _read_plan, runtime_files
    capsule = _read_plan(plan_path)
    if (not isinstance(capsule, dict) or capsule.get('schema') != 1
            or capsule.get('action') != 'runtime-upgrade'):
        raise ValueError('ランタイム更新計画の形式が不正です')
    target = Target(capsule['home'], capsule['profile'], capsule['mode'])
    if capsule.get('python') != str(target.python):
        raise ValueError('Pythonの配置が変わったため差分を再作成してください')
    files = runtime_files()
    with _lock(target):
        current = _capsule(target, files)
        if json.loads(encoded(current)) != capsule:
            raise ValueError('実行処理・導入情報・管理状態が変わったため差分を再作成してください')
        changed = {name: pair for name, pair in current['plans'].items() if pair[0] != pair[1]}
        if not changed:
            return {'outcome': 'upgraded', 'home': str(target.home), 'changed_files': 0}
        backups = target.path(DATA + 'backups')
        backups.mkdir(exist_ok=True, mode=0o700)
        os.chmod(backups, 0o700)
        folder = Path(tempfile.mkdtemp(prefix='runtime-upgrade-', dir=backups))
        write_atomic(folder / 'before.json', encoded({'state': current['state'], 'files': changed}))
        written = []
        try:
            for name, (before, after) in changed.items():
                target.guard()
                if (read_file(target.path(name)) != before
                        or read_file(target.path(STATE)) != current['state']):
                    raise ValueError('適用直前に設定が変わりました')
                written.append(name)
                write_atomic(target.path(name), after['text'], after['mode'])
        except Exception as error:
            try:
                for name in reversed(written):
                    before, after = changed[name]
                    path = target.path(name)
                    if read_file(path) not in (before, after):
                        raise ValueError('復旧中に実行処理の個別変更を検出しました')
                    if before is None:
                        path.unlink(missing_ok=True)
                    else:
                        write_atomic(path, before['text'], before['mode'])
            except Exception as rollback_error:
                raise ValueError(f'実行処理の更新と復元に失敗しました。バックアップを確認してください: {folder}') from rollback_error
            raise ValueError('実行処理の更新に失敗し、元の状態へ戻しました') from error
    return {'outcome': 'upgraded', 'home': str(target.home), 'changed_files': len(changed),
            'backup': str(folder)}
