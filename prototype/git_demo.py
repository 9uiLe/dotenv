"""Walk through Git receiving with a local stand-in, never a GitHub connection."""

from datetime import timedelta
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).absolute().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(REPOSITORY))

from prototype.demo import FIRST, NEXT, RULES, TOOLS, expect, hook_outputs, rules_and_skills, seed, targets, write
from prototype.engine import Manager
from prototype.git_fixtures import LocalRemote
from prototype.sandbox import create_sandbox


def main():
    root = create_sandbox(prefix='github-validation-')
    remote = LocalRemote(root)
    first_commit = remote.publish('v1')
    remote.configure()
    events = []
    checks = []
    later = NEXT + timedelta(days=1)

    def record(profile, step, result, outputs=None):
        event = {'pc': profile, 'step': step, 'result': result}
        if outputs is not None:
            event['hook_outputs'] = outputs
        events.append(event)
        checks.append(f'{profile}: {step}')

    def manager(profile):
        return Manager(root / profile, root / 'source', profile)

    def assert_source(profile, commit):
        state = manager(profile).status()
        assert state['active']['source']['commit'] == commit, state

    for profile in TOOLS:
        home = root / profile
        seed(home, profile)
        result = expect(manager(profile).sync(now=FIRST), 'updated')
        assert_source(profile, first_commit)
        rules_and_skills(home, profile, 'v1')
        record(profile, '10:00にローカルGitのv1を取得・配布', result,
               hook_outputs(home, profile, 'v1'))

    second_commit = remote.publish('v2')
    for profile in TOOLS:
        home = root / profile
        record(profile, '原本にv2があっても当日二重更新しない',
               expect(manager(profile).sync(now=FIRST), 'already_today'))
        assert_source(profile, first_commit)
        result = expect(manager(profile).sync(now=NEXT), 'updated')
        assert_source(profile, second_commit)
        rules_and_skills(home, profile, 'v2')
        record(profile, '翌日10:00に新commitのv2を取得・Hook実行', result,
               hook_outputs(home, profile, 'v2'))

    remote.reset(first_commit)
    for profile in TOOLS:
        home = root / profile
        before = targets(home)
        result = expect(manager(profile).sync(now=later), 'failed')
        assert targets(home) == before
        assert_source(profile, second_commit)
        record(profile, '共有元の履歴巻き戻しを拒否して配布済みv2を保持', result,
               hook_outputs(home, profile, 'v2'))
    remote.reset(second_commit)

    offline = root / 'offline.git'
    remote.remote.rename(offline)
    try:
        for profile in TOOLS:
            home = root / profile
            before = targets(home)
            result = expect(manager(profile).sync(now=later), 'failed')
            assert targets(home) == before
            assert_source(profile, second_commit)
            record(profile, '取得先に接続できなくても配布済みv2を保持', result,
                   hook_outputs(home, profile, 'v2'))
    finally:
        offline.rename(remote.remote)

    third_commit = remote.publish('v3')
    for profile in TOOLS:
        home = root / profile
        rules = home / RULES['codex']
        deployed = rules.read_text(encoding='utf-8')
        conflicted = deployed.replace('共有設定 v2', '共有設定 v2（配布先で直接編集）')
        assert conflicted != deployed
        rules.write_text(conflicted, encoding='utf-8')
        before = targets(home)
        result = expect(manager(profile).sync(now=later), 'failed')
        assert targets(home) == before
        assert_source(profile, second_commit)
        record(profile, 'v3取得後に配布先競合で停止し適用済みcommitはv2のまま', result,
               hook_outputs(home, profile, 'v2'))

        # Keep the conflict evidence, then simulate resolving the direct edit back to v2.
        write(root / f'evidence/{profile}-conflicted-AGENTS.md', conflicted)
        rules.write_text(deployed, encoding='utf-8')
        before, state = targets(home), manager(profile).status()
        result = expect(manager(profile).preview_sync(now=later), 'preview')
        assert result['version'] == 'v3'
        assert result['source']['commit'] == third_commit
        assert result['changes']
        assert targets(home) == before and manager(profile).status() == state
        assert_source(profile, second_commit)
        rules_and_skills(home, profile, 'v2')
        record(profile, '競合解消後にv3の差分だけ確認し設定と適用済みcommitは変更しない', result,
               hook_outputs(home, profile, 'v2'))

    limitations = [
        'GitHubの代わりに同じ模擬環境内のローカルGitを使用した',
        '本物のGitHubへの接続・認証・送信は行っていない',
        '10:00の日時を指定して実行し、日次のOS登録は行っていない',
        'AIアプリは起動せず、無副作用の架空Hookだけ子プロセスで実行した',
        '実際の設定を変更していない。生成物は削除せず保持した',
    ]
    report = {
        'scope': 'offline local Git and mock homes only', 'checks_passed': checks,
        'mock_pcs': {profile: str(root / profile) for profile in TOOLS},
        'mock_remote': str(remote.remote),
        'commits': {'v1': first_commit, 'v2': second_commit, 'v3': third_commit},
        'final_deployed_version': 'v2', 'final_preview_version': 'v3',
        'events': events, 'limitations': limitations,
    }
    write(root / 'report.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = [
        '# ローカルGit受信の模擬検証結果', '', f'{len(checks)}件の通し確認に成功しました。', '',
        'GitHubの代わりに同じ模擬環境内のGitリポジトリを使い、commitの取得から配布まで確認しました。',
        '更新予定は各Macの現地時刻で毎日10:00です。2026-10-20／21／22の日本時間を指定して検証しました。', '',
        '## 観測した結果', '',
        '- 原本のv1を取得して配布し、翌日10:00に新しいcommitのv2へ更新しました。',
        '- 原本の履歴巻き戻しと取得先の停止では、設定・スキル・Hookのv2を保持しました。',
        '- v3を取得できても配布先に競合があれば停止し、適用済みcommitはv2のままでした。',
        '- 模擬競合の記録を保存して解消後、v3の差分だけをプレビューしました。実際の配布先はv2を保持しました。',
        '- 個人MacのCodex、仕事MacのCodexとClaude Code向けHookを子プロセスで実行し、標準出力で共有設定の版を確認しました。', '',
        '| 模擬Mac | 確認内容 | 結果 |', '| --- | --- | --- |',
    ]
    lines.extend(f"| {event['pc']} | {event['step']} | {event['result']['outcome']} |" for event in events)
    lines.extend([
        '', '## commitと生成場所', '',
        f'- v1: `{first_commit}`', f'- v2: `{second_commit}`', f'- v3: `{third_commit}`',
        f'- 原本の代役: `{remote.remote}`', f'- 個人Mac: `{root / "personal"}`',
        f'- 仕事Mac: `{root / "work"}`', f'- 模擬競合の保存先: `{root / "evidence"}`',
        '- v3プレビューの具体的な差分とHook標準出力は同じ場所のreport.jsonに保持しています。', '',
        '## 今回の検証範囲', '',
    ])
    lines.extend('- ' + item for item in limitations)
    lines.append('')
    write(root / 'report.md', '\n'.join(lines))
    print(f'{len(checks)}件のローカルGit受信の模擬確認に成功しました。')
    print(f'確認結果: {root / "report.md"}')
    return root


if __name__ == '__main__':
    main()
