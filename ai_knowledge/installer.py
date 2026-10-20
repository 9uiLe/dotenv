"""Prepare private, read-only install plans and apply exactly the reviewed inputs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import stat

from ai_knowledge.runtime import LiveManager
from ai_knowledge.target import Target
from ai_knowledge.engine import SCHEDULE, checksum, encoded, read_file, validate_bundle, write_atomic
from ai_knowledge.git_source import receive
from ai_knowledge.sandbox import create_sandbox


REPOSITORY = Path(__file__).resolve().parents[1]
DATA = '.local/share/ai-knowledge/'
RUNTIME_FILES = ('__init__.py', '__main__.py', 'entrypoint.py', 'cli.py', 'installer.py',
                 'runtime.py', 'target.py', 'scheduler.py', 'source.py', 'git_config.py',
                 'git_dispatch.py', 'engine.py', 'engine_support.py', 'engine_settings.py',
                 'sandbox.py', 'git_source.py', 'hook_runner.py')


def runtime_files(root=REPOSITORY):
    files = {}
    for name in RUNTIME_FILES:
        path = root / 'ai_knowledge' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('配布用ランタイムのソースが不足しています')
        text = path.read_text(encoding='utf-8')
        compile(text, str(path), 'exec')
        files['ai_knowledge/' + name] = text
    return files


def _candidate(target, config):
    """Fetch away from the target; never create a cache in a live home for planning."""
    LiveManager(target).validate_source(config)
    scratch = create_sandbox(prefix='install-receive-')
    try:
        actual = dict(config)
        if target.mode == 'mock' and config['url'] == str(target.home.parent / 'remote.git'):
            shutil.copytree(target.home.parent / 'remote.git', scratch / 'remote.git', symlinks=True)
            actual['url'] = str(scratch / 'remote.git')
        received = receive(scratch / 'personal', actual)
        received['source']['url'] = config['url']
        received['bundle'] = validate_bundle(received['bundle'])
        if target.mode == 'live':
            from ai_knowledge.source import require_ci_success
            received['ci'] = require_ci_success(received['source'])
        return received
    finally:
        shutil.rmtree(scratch)


def _extras(target, config, files):
    installation = {
        'schema': 1, 'home': str(target.home), 'profile': target.profile,
        'mode': target.mode, 'python': str(target.python),
        'runtime_hashes': {name: checksum(text) for name, text in files.items()},
    }
    extras = {DATA + 'runtime/' + name: {'text': text, 'mode': 0o600}
              for name, text in files.items()}
    extras[DATA + 'installation.json'] = {'text': encoded(installation), 'mode': 0o600}
    extras[DATA + 'source/git.json'] = {'text': encoded(config), 'mode': 0o600}
    return extras


class _InstallManager(LiveManager):
    def __init__(self, target, received, files):
        super().__init__(target)
        self.received = received
        self.files = files
        self.reviewed_plans = None

    def _receive(self, state):
        return deepcopy(self.received['bundle']), deepcopy(self.received['source'])

    def _plan(self, bundle, state, allow_removal=False):
        if state['active'] is not None or self.state_path.exists():
            raise ValueError('導入済みまたは中断済みです。状態確認・復元を先に行ってください')
        managed, plans = super()._plan(bundle, state, allow_removal=allow_removal)
        config = {key: self.received['source'][key] for key in ('url', 'branch', 'manifest')}
        for name, after in _extras(self.target, config, self.files).items():
            before = read_file(self._path(name))
            if before is not None:
                raise ValueError('導入先に既存のランタイムまたは受信設定があります')
            plans[name] = (None, after)
        plans[SCHEDULE] = self._schedule_plan(state, True)
        if self.reviewed_plans is not None and json.loads(encoded(plans)) != self.reviewed_plans:
            raise ValueError('適用直前に既存設定が変わったため差分を再作成してください')
        return managed, plans


def _plans(target, received, files):
    manager = _InstallManager(target, received, files)
    state = manager._state()
    _, plans = manager._plan(received['bundle'], state)
    plans[SCHEDULE] = manager._schedule_plan(state, True)
    return manager, plans


def create_plan(target, config):
    """Read existing settings and export a private patch outside the selected home."""
    target.guard()
    files = runtime_files()
    received = _candidate(target, config)
    manager, plans = _plans(target, received, files)
    capsule = {
        'schema': 1, 'home': str(target.home), 'profile': target.profile, 'mode': target.mode,
        'python': str(target.python), 'config': config, 'received': received,
        'runtime_hashes': {name: checksum(text) for name, text in files.items()},
        'plans': plans,
    }
    folder = create_sandbox(prefix='mac-install-plan-')
    os.chmod(folder, 0o700)
    plan_path, patch_path, report_path = (folder / name for name in ('plan.json', 'changes.patch', 'report.md'))
    changes = manager._changes(plans)
    write_atomic(plan_path, encoded(capsule))
    write_atomic(patch_path, ''.join(change['diff'] for change in changes))
    revision = checksum(encoded(received['bundle']))
    administrative = [DATA + 'state.json', DATA + 'operation.lock', DATA + 'pending.json',
                      DATA + 'backups/', DATA + 'releases/' + revision + '/', DATA + 'retired/',
                      DATA + 'receive-cache.git/', DATA + 'sync.log', DATA + 'sync-error.log']
    lines = ['# Macへの導入差分（未適用）', '',
             f'対象: `{target.home}` / `{target.profile}` / `{target.mode}`', '',
             f"共有元: `{config['url']}` の `{config['branch']}`、コミット `{received['source']['commit']}`。", '',
             'この操作は対象の設定を書き換えず、OSへ日次処理を登録していません。', '',
             '[全ファイルの変更差分](changes.patch) / [適用時の照合データ](plan.json)', '',
             '## 追加・変更するファイル', '', '| 対象 | 操作 |', '| --- | --- |']
    lines += [f"| `{change['path']}` | {change['action']} |" for change in changes]
    lines += ['', '## 実行時の管理データ', '',
              '以下も同じ専用フォルダ内に作成します。バックアップと管理状態は本人だけが読み取れる権限にします。', '']
    lines += ['- `' + str(target.home / name) + '`' for name in administrative]
    lines += ['', '## 適用条件と停止・復元', '',
              '適用前に共有元・受信コミット・ランタイム・既存設定を再照合し、差分作成後に変わっていれば停止します。', '',
              '毎日10:00（各Macの現地時刻）とログイン時に起動します。10時前と成功済みの同日は更新しません。', '',
              'pause-syncで更新停止後にLaunchAgentを解除、pause-hooksで共有Hookだけ停止します。', '',
              'restoreは旧版、uninstallは共有部分の取り外しです。いずれも初期値は差分表示で、--apply時は更新と共有Hookを止めます。', '',
              '初回適用後の登録に失敗した場合、設定とランタイムは残し、自動更新を停止して失敗を報告します。', '',
              '実際のMacでの初回適用は、この差分を確認してから別途行ってください。', '']
    write_atomic(report_path, '\n'.join(lines))
    return {'outcome': 'preview', 'plan': str(plan_path), 'patch': str(patch_path),
            'report': str(report_path), 'changes': [{'path': x['path'], 'action': x['action']} for x in changes],
            'source': received['source'], 'live_settings_changed': False}


def _read_plan(plan_path):
    path = Path(os.path.abspath(Path(plan_path).expanduser()))
    if any(candidate.is_symlink() for candidate in (path, *path.parents)):
        raise ValueError('導入計画のリンクは使用できません')
    folder = path.parent.stat()
    if folder.st_uid != os.getuid() or stat.S_IMODE(folder.st_mode) != 0o700:
        raise ValueError('導入計画は本人専用のフォルダに置いてください')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 16 * 1024 * 1024):
            raise ValueError('導入計画は本人だけが読み書きできる通常ファイルに限定しています')
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('導入計画が読取上限を超えています')
    return json.loads(raw.decode('utf-8'))


def apply_plan(plan_path, scheduler=None):
    capsule = _read_plan(plan_path)
    if not isinstance(capsule, dict) or capsule.get('schema') != 1:
        raise ValueError('導入計画の形式が不正です')
    target = Target(capsule['home'], capsule['profile'], capsule['mode'])
    if str(target.python) != capsule['python']:
        raise ValueError('Pythonの配置が変わったため差分を再作成してください')
    files = runtime_files()
    if {name: checksum(text) for name, text in files.items()} != capsule['runtime_hashes']:
        raise ValueError('ランタイムが変わったため差分を再作成してください')
    received = _candidate(target, capsule['config'])
    if any(received[key] != capsule['received'][key] for key in ('bundle', 'source')):
        raise ValueError('共有元が更新されたため差分を再作成してください')
    manager, plans = _plans(target, received, files)
    # JSON round trip normalizes the tuple pairs used by the engine.
    if json.loads(encoded(plans)) != capsule['plans']:
        raise ValueError('既存設定が変わったため差分を再作成してください')
    manager.reviewed_plans = capsule['plans']
    from ai_knowledge.cli import _schedule_operation
    prior_schedule = _schedule_operation(target, 'status', scheduler)
    if prior_schedule['outcome'] == 'failed' or prior_schedule.get('registered'):
        raise ValueError('日次処理の状態が不明、または同じ名前の処理が既に登録されています')
    result = manager.sync(force=True)
    if result['outcome'] == 'failed':
        return result
    registration = _schedule_operation(target, 'register', scheduler)
    if registration['outcome'] == 'failed':
        stopped = LiveManager(target).pause_sync()
        confirmed = stopped['outcome'] != 'failed'
        return {'outcome': 'failed',
                'reason': ('初回配布後の日次登録に失敗し、更新を停止しました' if confirmed else
                           '日次登録と更新停止を確認できません。処理終了後にpause-syncを再試行してください'),
                'scheduler': registration, 'installed': True, 'sync_stop_confirmed': confirmed,
                'pause_result': stopped}
    return {'outcome': 'installed', 'home': str(target.home), 'version': received['bundle']['version'],
            'source': received['source'], 'scheduler': registration}
