"""Rehearse the installed Mac lifecycle without registering a real OS job."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

from ai_knowledge.installer import apply_plan, create_plan
from ai_knowledge.runtime import LiveManager
from ai_knowledge.target import Target
from prototype.engine import SCHEDULE, encoded, write_atomic
from prototype.git_fixtures import LocalRemote
from prototype.sandbox import create_sandbox


def main():
    root = create_sandbox(prefix='mac-validation-')
    os.chmod(root, 0o700)
    remote = LocalRemote(root)
    remote.publish('v1')
    config = remote.configure()
    checks = []
    plans = {}

    def record(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({'check': name, 'result': 'passed'})

    def command(target, *arguments):
        result = subprocess.run(
            [str(target.python), '-B', str(target.runtime / 'ai_knowledge/entrypoint.py'), *arguments],
            cwd=root, input='{}', capture_output=True, text=True, timeout=60, check=True,
        )
        return json.loads(result.stdout)

    targets = [Target.mock(root / profile) for profile in ('personal', 'work')]
    for target in targets:
        local = target.home / '.codex/AGENTS.md'
        write_atomic(local, 'このMacだけの指示を保持します。\n')
        if target.profile == 'work':
            write_atomic(target.home / '.claude/settings.json', encoded({'localSetting': 'retain', 'hooks': {
                'SessionStart': [{'hooks': [{'type': 'command', 'command': 'echo local-only'}]}]}}))
        before = local.read_bytes()
        plans[target.profile] = create_plan(target, config)
        record(target.profile + ': 差分作成では既存設定と管理フォルダを変更しない',
               local.read_bytes() == before and not target.data.exists())
        record(target.profile + ': 初回配布と模擬OS登録', apply_plan(plans[target.profile]['plan'])['outcome'] == 'installed')
        schedule = plistlib.loads((target.home / SCHEDULE).read_bytes())
        record(target.profile + ': 毎日10:00・ログイン時起動のplist',
               schedule['StartCalendarInterval'] == {'Hour': 10, 'Minute': 0} and schedule['RunAtLoad'])
        record(target.profile + ': 別プロセスの配布済みHook',
               '共有設定 v1' in encoded(command(target, 'hook', '--tool', 'codex', '--hook', 'shared-version')))

    remote.publish('v2')
    for target in targets:
        state = LiveManager(target).status()
        tomorrow = datetime.fromisoformat(state['last_success_at']) + timedelta(days=1)
        morning = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        before = morning - timedelta(minutes=1)
        record(target.profile + ': 09:59は更新しない', command(target, 'sync', '--at', before.isoformat())['outcome'] == 'not_due')
        record(target.profile + ': 翌日10:00にv2を受信', command(target, 'sync', '--at', morning.isoformat())['version'] == 'v2')
        record(target.profile + ': 同日の更新は重複しない', command(target, 'sync', '--at', morning.isoformat())['outcome'] == 'already_today')
        command(target, 'pause-hooks')
        command(target, 'pause-sync')
        record(target.profile + ': 停止中は予定日でも受信しない',
               command(target, 'sync', '--at', (morning + timedelta(days=1)).isoformat())['outcome'] == 'paused')
        command(target, 'resume-sync')
        record(target.profile + ': 更新だけの再開では共有Hookを再開しない',
               command(target, 'status')['hooks_enabled'] is False)
        preview = command(target, 'restore')
        record(target.profile + ': 復元プレビューは状態を変更しない',
               preview['outcome'] == 'preview' and command(target, 'status')['version'] == 'v2')
        record(target.profile + ': 前の共有版へ復元', command(target, 'restore', '--apply')['outcome'] == 'restored')
        status = command(target, 'status')
        record(target.profile + ': 復元後は更新・共有Hook・OS登録が停止',
               status['version'] == 'v1' and not status['sync_enabled'] and not status['hooks_enabled']
               and not status['scheduler']['registered'])
        command(target, 'resume-hooks')
        record(target.profile + ': 復元したv1のHookを実行',
               '共有設定 v1' in encoded(command(target, 'hook', '--tool', 'codex', '--hook', 'shared-version')))
        record(target.profile + ': 共有部分の取り外し', command(target, 'uninstall', '--apply')['outcome'] == 'uninstalled')
        record(target.profile + ': 元の指示と個別設定を保持',
               (target.home / '.codex/AGENTS.md').read_text() == 'このMacだけの指示を保持します。\n'
               and (target.profile != 'work' or json.loads((target.home / '.claude/settings.json').read_text())['localSetting'] == 'retain'))

    report = {'checks': checks, 'plans': plans, 'actual_settings_changed': False, 'actual_launchctl_called': False}
    write_atomic(root / 'report.json', encoded(report))
    lines = ['# Mac導入・日次更新・停止・復元の模擬検証', '',
             f'{len(checks)}項目成功。実設定の変更・実OSへの登録・GitHubへの送信は行っていません。', '',
             '日付を指定して09:59・翌日10:00を試験し、OS登録は模擬アダプターで検証しました。実際のスリープ復帰は未検証です。', '',
             '| 確認項目 | 結果 |', '| --- | --- |']
    lines += [f"| {check['check']} | 成功 |" for check in checks]
    lines += ['', '## 模擬環境の導入差分', '']
    lines += [f"- [{profile}の差分説明]({plan['report']})" for profile, plan in plans.items()]
    write_atomic(root / 'report.md', '\n'.join(lines) + '\n')
    print(json.dumps({'passed': len(checks), 'report': str(root / 'report.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
