"""One readable, offline walkthrough; every generated file is retained."""

from datetime import datetime
import json
from pathlib import Path
import plistlib
import stat
import subprocess
import sys


REPOSITORY = Path(__file__).absolute().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(REPOSITORY))

from prototype.engine import Manager
from prototype.fixtures import make_bundle
from prototype.sandbox import create_sandbox


TOOLS = {'personal': ('codex',), 'work': ('codex', 'claude')}
RULES = {'codex': '.codex/AGENTS.md', 'claude': '.claude/CLAUDE.md'}
SETTINGS = {'codex': '.codex/hooks.json', 'claude': '.claude/settings.json'}
LOCAL_HOOK = {'hooks': [{'type': 'command', 'command': '/usr/bin/true'}]}
FIRST = datetime.fromisoformat('2026-10-20T10:00:00+09:00')
NEXT = datetime.fromisoformat('2026-10-21T10:00:00+09:00')
SCHEDULE = 'Library/LaunchAgents/local.ai-knowledge.sync.plist'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def write_bundle(root, version):
    write(root / 'source/bundle.json', json.dumps(make_bundle(version), ensure_ascii=False, indent=2) + '\n')


def seed(home, profile):
    for tool in TOOLS[profile]:
        write(home / RULES[tool], f'# このMacだけの指示\n{profile}専用の指示を保持する。\n')
        write(home / SETTINGS[tool], json.dumps({
            'localPreference': profile,
            'hooks': {'SessionStart': [LOCAL_HOOK]},
        }, ensure_ascii=False, indent=2) + '\n')
    write(home / '.codex/config.toml', 'model = "local-example"\n')
    write(home / 'local-only.txt', f'{profile}だけのファイル\n')


def targets(home):
    # Private state/backups may record a preview or failure. User-facing targets must stay intact.
    return {
        path.relative_to(home).as_posix(): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in home.rglob('*')
        if path.is_file() and path.relative_to(home).parts[0] != '.local'
    }


def hook_outputs(home, profile, version):
    outputs = {}
    for tool in TOOLS[profile]:
        completed = subprocess.run(
            [sys.executable, str(REPOSITORY / 'prototype/hook_runner.py'),
             '--home', str(home), '--tool', tool, '--hook', 'shared-version'],
            input=json.dumps({'hook_event_name': 'SessionStart'}), text=True,
            capture_output=True, check=True, timeout=15,
        )
        output = json.loads(completed.stdout)
        expected = {} if version is None else {'hookSpecificOutput': {
            'hookEventName': 'SessionStart', 'additionalContext': '共有設定 ' + version,
        }}
        if output != expected:
            raise AssertionError(f'{profile}/{tool}: Hook出力が想定と異なります: {output}')
        outputs[tool] = output
    return outputs


def rules_and_skills(home, profile, version):
    for tool in TOOLS[profile]:
        rules = (home / RULES[tool]).read_text(encoding='utf-8')
        skill = (home / ('.agents' if tool == 'codex' else '.claude') /
                 'skills/ai-knowledge-example/SKILL.md').read_text(encoding='utf-8')
        assert f'{profile}専用の指示' in rules
        assert '共有設定 ' + version in rules and '共有設定 ' + version in skill
        settings = json.loads((home / SETTINGS[tool]).read_text(encoding='utf-8'))
        assert settings['localPreference'] == profile
        assert LOCAL_HOOK in settings['hooks']['SessionStart']
    assert (home / 'local-only.txt').read_text() == f'{profile}だけのファイル\n'
    assert (home / '.codex/config.toml').read_text() == 'model = "local-example"\n'
    if profile == 'personal':
        assert not (home / '.claude').exists()


def expect(result, outcome):
    if result['outcome'] != outcome:
        raise AssertionError(f'想定: {outcome}; 実際: {result}')
    return result


def main():
    root = create_sandbox(prefix='validation-')
    write_bundle(root, 'v1')
    checks = []
    events = []
    initial = {}

    def record(profile, step, result, hooks=None):
        entry = {'pc': profile, 'step': step, 'result': result}
        if hooks is not None:
            entry['hook_outputs'] = hooks
        events.append(entry)
        checks.append(f'{profile}: {step}')

    for profile in TOOLS:
        home = root / profile
        seed(home, profile)
        initial[profile] = targets(home)
        manager = Manager(home, root / 'source', profile)
        before = datetime.fromisoformat('2026-10-20T09:59:00+09:00')
        record(profile, '09:59は更新しない', expect(manager.sync(now=before), 'not_due'))
        record(profile, '10:00にv1を配布・Hook実行', expect(manager.sync(now=FIRST), 'updated'),
               hook_outputs(home, profile, 'v1'))
        rules_and_skills(home, profile, 'v1')
        plist = plistlib.loads((home / SCHEDULE).read_bytes())
        assert plist['StartCalendarInterval'] == {'Hour': 10, 'Minute': 0}
        assert plist['RunAtLoad'] is True

    write_bundle(root, 'v2')
    for profile in TOOLS:
        home = root / profile
        manager = Manager(home, root / 'source', profile)
        record(profile, '同日は二重更新しない', expect(manager.sync(now=FIRST), 'already_today'),
               hook_outputs(home, profile, 'v1'))
        # A newly constructed manager demonstrates persisted state across executions.
        manager = Manager(home, root / 'source', profile)
        record(profile, '翌日10:00にv2を配布・Hook実行', expect(manager.sync(now=NEXT), 'updated'),
               hook_outputs(home, profile, 'v2'))
        rules_and_skills(home, profile, 'v2')
        record(profile, '共有Hookだけを停止', expect(manager.pause_hooks(), 'changed'),
               hook_outputs(home, profile, None))
        expect(manager.pause_sync(), 'changed')
        assert not (home / SCHEDULE).exists()
        record(profile, '更新停止中は強制指定でも配布しない', expect(manager.sync(now=NEXT, force=True), 'paused'))
        expect(manager.resume_sync(), 'changed')
        assert (home / SCHEDULE).exists()
        assert manager.status()['hooks_enabled'] is False
        record(profile, '更新再開でHook停止は解除されない', manager.status(),
               hook_outputs(home, profile, None))
        record(profile, 'Hookを明示再開するとv2で動作', expect(manager.resume_hooks(), 'changed'),
               hook_outputs(home, profile, 'v2'))

        before_targets, before_status = targets(home), manager.status()
        result = expect(manager.restore(), 'preview')
        assert targets(home) == before_targets and manager.status() == before_status
        record(profile, '前の版への復元プレビューは変更しない', result)
        result = expect(manager.restore(apply=True), 'restored')
        assert manager.status()['sync_enabled'] is False and manager.status()['hooks_enabled'] is False
        assert not (home / SCHEDULE).exists()
        rules_and_skills(home, profile, 'v1')
        record(profile, 'v1へ復元し更新とHookを停止', result, hook_outputs(home, profile, None))
        record(profile, '復元後にHookを明示再開するとv1で動作', expect(manager.resume_hooks(), 'changed'),
               hook_outputs(home, profile, 'v1'))

        before_targets, before_status = targets(home), manager.status()
        result = expect(manager.uninstall(), 'preview')
        assert targets(home) == before_targets and manager.status() == before_status
        record(profile, '導入取り消しプレビューは変更しない', result)
        result = expect(manager.uninstall(apply=True), 'uninstalled')
        assert manager.status()['active'] is None
        assert manager.status()['sync_enabled'] is False and manager.status()['hooks_enabled'] is False
        assert not (home / SCHEDULE).exists()
        after = targets(home)
        assert set(after) == set(initial[profile])
        for relative, original in initial[profile].items():
            if relative in SETTINGS.values():
                assert json.loads(after[relative][0]) == json.loads(original[0])
                assert after[relative][1] == original[1]
            else:
                assert after[relative] == original
        record(profile, '導入前へ戻しPC固有の指示と既存Hookを保持', result,
               hook_outputs(home, profile, None))

    failure_root = create_sandbox(prefix='validation-failure-')
    write_bundle(failure_root, 'v1')
    home = failure_root / 'personal'
    seed(home, 'personal')
    manager = Manager(home, failure_root / 'source', 'personal')
    expect(manager.sync(now=FIRST), 'updated')
    before_targets = targets(home)
    write(failure_root / 'source/bundle.json', '{invalid sample source\n')
    result = expect(manager.sync(now=NEXT), 'failed')
    assert targets(home) == before_targets
    record('failure-personal', '共有元の形式不正でも配布先とv1のHookを保持', result,
           hook_outputs(home, 'personal', 'v1'))
    write_bundle(failure_root, 'v2')
    result = expect(manager.sync(now=NEXT, fail_after=1), 'failed')
    assert targets(home) == before_targets
    record('failure-personal', '1件書き込み後の障害を旧状態へ復旧', result,
           hook_outputs(home, 'personal', 'v1'))

    report = {
        'scope': 'offline mock homes only', 'checks_passed': checks,
        'schedule': {'hour': 10, 'minute': 0, 'timezone': 'each Mac local time'},
        'mock_pcs': {name: str(root / name) for name in TOOLS},
        'failure_sandbox': str(failure_root), 'events': events,
        'limitations': ['日次実行用plistは生成しただけで常駐登録していない',
                        'GitHub受信は未実装', 'AIアプリは起動していない', '実際の設定は変更していない'],
    }
    write(root / 'report.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = [
        '# 模擬環境での通し検証結果', '', f'{len(checks)}件の確認に成功しました。', '',
        '- 更新予定: 各Macの現地時刻で毎日10:00。試験時刻は2026-10-20／21の日本時間。',
        '- 観測した流れ: v1 → 翌日10:00にv2 → 復元してv1 → 導入前。',
        '- 配布対象: 共通ルール、スキル、Hook登録、Hook用スクリプト。',
        '- 個人MacはCodex、仕事MacはCodexとClaude Code向けの配布を確認。', '',
        '## 実行結果', '', '| 模擬Mac | 確認内容 | 結果 |', '| --- | --- | --- |',
    ]
    lines.extend(f"| {entry['pc']} | {entry['step']} | {entry['result'].get('outcome', '状態確認')} |"
                 for entry in events)
    lines.extend([
        '', '## Hookの実出力', '',
        'Pythonの子プロセスとして配布済みスクリプトを実行し、標準出力を比較しました。',
        '`hookSpecificOutput.additionalContext` は `共有設定 v1` → `共有設定 v2` → '
        '`共有設定 v1` と変化しました。共有Hook停止中と導入取り消し後は `{}` でした。',
        '既存Hookは設定に残しており、このデモでは既存Hook自体を実行していません。', '',
        '## 生成場所', '',
        f'- 個人Mac: `{root / "personal"}`', f'- 仕事Mac: `{root / "work"}`',
        f'- 障害の模擬環境: `{failure_root}`',
        '- 既存設定のバックアップ、退避した共有ファイル、各確認の詳細は生成場所とreport.jsonに保持しています。',
        '', '## 今回の検証範囲', '',
        '毎日10:00のplistを模擬Mac内に生成し内容を検査しました。OSへの登録・読み込みは行っていません。',
        '更新時刻は指定した日時で模擬実行しており、スリープ復帰や実時刻での自動起動は未検証です。',
        '共有元はローカルファイルで、GitHubからの受信は未実装です。AIアプリは起動していません。',
        '**実際の設定は変更していません。** 通信・送信・公開・費用の発生はありません。',
        '生成物は削除せず保持しています。', '',
    ])
    write(root / 'report.md', '\n'.join(lines))
    print(f'{len(checks)}件の模擬確認に成功しました。')
    print(f'確認結果: {root / "report.md"}')
    return root


if __name__ == '__main__':
    main()
