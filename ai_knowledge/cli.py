"""Explicit installation, private previews, and installed lifecycle commands."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from ai_knowledge.runtime import LiveManager, run_hook
from ai_knowledge.scheduler import FakeLaunchctl, Scheduler
from ai_knowledge.target import Target
from ai_knowledge.engine import encoded, write_atomic


def _schedule_operation(target, operation, scheduler=None):
    target.guard()
    fake = None
    if scheduler is None:
        if target.mode == 'mock':
            state_file = target.path('.local/share/ai-knowledge/scheduler.json')
            registered = json.loads(state_file.read_text()).get('registered') if state_file.exists() else False
            if type(registered) is not bool:
                raise ValueError('模擬スケジューラーの状態が不正です')
            fake = FakeLaunchctl(registered=registered)
            scheduler = Scheduler(target.home, runner=fake)
        else:
            scheduler = Scheduler(target.home)
    result = getattr(scheduler, operation)()
    if fake is not None and operation != 'status':
        write_atomic(state_file, encoded({'registered': fake.registered, 'last_calls': fake.calls}))
    return result


def _summary(state):
    active = state.get('active') or {}
    return {key: state.get(key) for key in ('phase', 'sync_enabled', 'hooks_enabled',
            'last_success_date', 'last_success_at', 'last_error')} | {
                'version': active.get('bundle', {}).get('version'), 'source': active.get('source'),
                'previous_versions': len(state['history'])}


def operate(target, command, scheduler=None, at=None, apply=False):
    installation = target.path('.local/share/ai-knowledge/installation.json')
    if not installation.is_file():
        if command == 'status':
            return {'outcome': 'not_installed'}
        raise ValueError('先に導入差分を作成し、初回導入を完了してください')
    _installed_target(installation)
    manager = LiveManager(target)
    if at is not None and target.mode != 'mock':
        raise ValueError('模擬時刻は模擬環境だけで指定できます')
    if command == 'status':
        return {'outcome': 'status', **_summary(manager.status()),
                'scheduler': _schedule_operation(target, 'status', scheduler)}
    if command == 'sync':
        result = manager.sync(now=at)
        changes = result.pop('changes', [])
        result['changed_files'] = len(changes)
        return result
    if command == 'reinstall':
        if manager.status()['phase'] != 'uninstalled':
            raise ValueError('再導入は共有部分を取り外した状態だけで実行できます')
        if not apply:
            result = manager.preview_sync()
            result.pop('bundle', None)
            return result
        enabled = manager.resume_sync()
        if enabled['outcome'] == 'failed':
            return enabled
        result = manager.sync(force=True)
        result.pop('changes', None)
        if result['outcome'] == 'failed':
            stopped = manager.pause_sync()
            result['sync_stop_confirmed'] = stopped['outcome'] != 'failed'
            return result
        registered = _schedule_operation(target, 'register', scheduler)
        if registered['outcome'] == 'failed':
            stopped = manager.pause_sync()
            return {'outcome': 'failed', 'reason': '再導入後の日次登録に失敗しました',
                    'sync_stop_confirmed': stopped['outcome'] != 'failed', 'scheduler': registered}
        return {'outcome': 'reinstalled', 'version': result['version'], 'source': result['source'],
                'scheduler': registered, 'hooks_enabled': manager.status()['hooks_enabled']}
    if command in ('pause-hooks', 'resume-hooks'):
        return getattr(manager, command.replace('-', '_'))()
    if command == 'pause-sync':
        result = manager.pause_sync()
        if result['outcome'] != 'failed':
            result['scheduler'] = _schedule_operation(target, 'unregister', scheduler)
            if result['scheduler']['outcome'] == 'failed':
                result['outcome'] = 'failed'
                result['reason'] = '更新フラグは停止しましたが、OS登録を解除できませんでした'
        return result
    if command == 'resume-sync':
        if manager.status()['phase'] == 'uninstalled':
            raise ValueError('取り外し済みのため更新を再開できません')
        result = manager.resume_sync()
        if result['outcome'] != 'failed':
            result['scheduler'] = _schedule_operation(target, 'register', scheduler)
            if result['scheduler']['outcome'] == 'failed':
                stopped = manager.pause_sync()
                result['outcome'] = 'failed'
                result['sync_stop_confirmed'] = stopped['outcome'] != 'failed'
                result['pause_result'] = stopped
                result['reason'] = ('日次登録に失敗したため、更新を停止しました' if result['sync_stop_confirmed'] else
                                    '日次登録と更新停止を確認できません。処理終了後にpause-syncを再試行してください')
        return result
    if command in ('restore', 'uninstall'):
        if not apply:
            return getattr(manager, command)()
        stopped = operate(target, 'pause-sync', scheduler=scheduler)
        if stopped['outcome'] == 'failed':
            return stopped
        hooks_stopped = manager.pause_hooks()
        if hooks_stopped['outcome'] == 'failed':
            return hooks_stopped
        return getattr(manager, command)(apply=True)
    raise ValueError('不明な操作です')


def _installed_target(path):
    path = Path(path)
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('schema') != 1:
        raise ValueError('導入情報が不正です')
    target = Target(document['home'], document['profile'], document['mode'])
    if path != target.path('.local/share/ai-knowledge/installation.json'):
        raise ValueError('導入情報の場所が一致しません')
    if document['python'] != str(target.python):
        raise ValueError('導入時のPythonと異なります。配置を確認してください')
    from ai_knowledge.installer import runtime_files
    from ai_knowledge.engine import checksum
    if {name: checksum(text) for name, text in runtime_files(root=target.runtime).items()} != document['runtime_hashes']:
        raise ValueError('導入済みの実行処理が変更されています。更新とHookを実行せず確認してください')
    return target


def parser():
    result = argparse.ArgumentParser(description='Macの共有設定。導入は差分を作成してから明示適用します。')
    result.add_argument('--sandbox', type=Path, help='模擬ホームの親フォルダ。実際のOS登録はしません')
    result.add_argument('--profile', choices=('personal', 'work'), default='personal')
    commands = result.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='既存設定を読み取り、導入差分を私有フォルダへ保存')
    plan.add_argument('--url', default='https://github.com/9uiLe/dotenv.git')
    plan.add_argument('--branch', default='master')
    install = commands.add_parser('apply', help='照合済みの導入差分を適用し日次更新を登録')
    install.add_argument('--plan', type=Path, required=True)
    install.add_argument('--apply', action='store_true', required=True)
    sync = commands.add_parser('sync', help='通常の日次判定で共有元を受信し適用')
    sync.add_argument('--at', type=datetime.fromisoformat, help='模擬環境の試験日時だけ指定可能')
    for name in ('status', 'pause-sync', 'resume-sync', 'pause-hooks', 'resume-hooks'):
        commands.add_parser(name)
    for name in ('restore', 'uninstall', 'reinstall'):
        undo = commands.add_parser(name, help='初期値は差分表示。--applyで共有部分だけ変更')
        undo.add_argument('--apply', action='store_true')
    hook = commands.add_parser('hook')
    hook.add_argument('--tool', choices=('codex', 'claude'), required=True)
    hook.add_argument('--hook', required=True)
    return result


def main(argv=None, installation=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'apply':
            from ai_knowledge.installer import apply_plan
            result = apply_plan(args.plan)
        else:
            target = (_installed_target(installation) if installation is not None else
                      Target.mock(args.sandbox / args.profile, args.profile) if args.sandbox else
                      Target.live(args.profile))
            if args.command == 'plan':
                from ai_knowledge.installer import create_plan
                result = create_plan(target, {'url': args.url, 'branch': args.branch, 'manifest': 'shared/manifest.json'})
            elif args.command == 'hook':
                return run_hook(target, args.tool, args.hook)
            else:
                result = operate(target, args.command, at=getattr(args, 'at', None), apply=getattr(args, 'apply', False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'outcome': 'failed', 'reason': str(error)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('outcome') == 'failed' else 0
