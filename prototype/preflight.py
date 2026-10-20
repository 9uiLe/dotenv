"""Legacy projection; use ai_knowledge plan for a complete, applicable install plan."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import stat
import sys

from prototype.engine import Manager, RULES, SCHEDULE, SKILLS, checksum, encoded, format_diff, validate_bundle, write_atomic
from prototype.sandbox import create_sandbox


class _ProjectedManager(Manager):
    def _hook_entries(self, bundle, tool):
        entries = super()._hook_entries(bundle, tool)
        for entry in entries:
            for handler in entry['group']['hooks']:
                arguments = shlex.split(handler['command'])
                arguments[1] = str(self.live_home / '.local/share/ai-knowledge/runtime/hook_runner.py')
                arguments[3] = str(self.live_home)
                handler['command'] = shlex.join(arguments)
        return entries

    def _schedule(self):
        return plistlib.dumps({
            'Label': 'local.ai-knowledge.sync', 'RunAtLoad': True,
            'StartCalendarInterval': {'Hour': 10, 'Minute': 0},
            'ProgramArguments': [sys.executable,
                                 str(self.live_home / '.local/share/ai-knowledge/runtime/sync.py')],
        }).decode()


def _without_links(path):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('既存のリンクを見つけたため導入差分を保留しました')


def _read_only(path):
    _without_links(path)
    if not path.exists():
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            raise ValueError('既存設定が通常ファイルでないか、読取上限を超えています')
        contents = stream.read(1024 * 1024 + 1)
    if len(contents) > 1024 * 1024:
        raise ValueError('既存設定が読取上限を超えています')
    return contents.decode('utf-8')


def project_installation(live_home, bundle, source, profiles=('personal', 'work')):
    """Project both profiles against one machine; never write to live_home."""
    live_home = Path(os.path.abspath(Path(live_home).expanduser()))
    bundle = validate_bundle(deepcopy(bundle))
    if not set(profiles) <= {'personal', 'work'} or not profiles:
        raise ValueError('プロファイルが不正です')
    root = create_sandbox(prefix='installation-preview-')
    os.chmod(root, 0o700)
    evidence = root / 'evidence'
    evidence.mkdir(mode=0o700)
    baselines = {}
    results = {}
    for profile in profiles:
        manager = _ProjectedManager(root / profile, root / 'source', profile)
        manager.live_home = live_home
        try:
            relatives = [RULES[tool] for tool in manager.tools]
            relatives += ['.codex/config.toml', '.codex/hooks.json', SCHEDULE]
            if profile == 'work':
                relatives.append('.claude/settings.json')
            for relative in relatives:
                original = live_home / relative
                text = _read_only(original)
                baselines[str(original)] = None if text is None else hashlib.sha256(text.encode()).hexdigest()
                if text is not None:
                    write_atomic(manager.home / relative, text)
            override = _read_only(live_home / '.codex/AGENTS.override.md')
            if override and override.strip():
                raise ValueError('CodexのAGENTS.override.mdがあるため、指示の優先順位の確認が必要です')
            for tool in manager.tools:
                for name in bundle['files']:
                    if name.startswith('skills/'):
                        folder = live_home / SKILLS[tool] / name.split('/')[1]
                        _without_links(folder)
                        if folder.exists() or folder.is_symlink():
                            raise ValueError('同名の共有スキルが既にあるため、自動置換せず保留しました')
            state = manager._default()
            _, plans = manager._plan(bundle, state)
            plans[SCHEDULE] = manager._schedule_plan(state, True)
            for name, text in bundle['files'].items():
                if name.startswith('hooks/'):
                    plans['.local/share/ai-knowledge/releases/' + checksum(encoded(bundle)) + '/' + name] = (None, {'text': text, 'mode': 0o644})
            plans['.local/share/ai-knowledge/source/git.json'] = (
                None, {'text': encoded({k: source[k] for k in ('url', 'branch', 'manifest')}), 'mode': 0o600})
            changes = []
            patch = []
            for relative, (before, after) in sorted(plans.items()):
                if before == after:
                    continue
                target = str(live_home / relative)
                changes.append({'path': target, 'action': 'create' if before is None else 'update'})
                patch.append(format_diff((before or {}).get('text', ''), (after or {}).get('text', ''),
                                         target + ' (現在)', target + ' (導入案)'))
            patch_path = evidence / (profile + '.proposed.patch')
            write_atomic(patch_path, '# 旧試作形式の導入案のため適用不可。ai_knowledge planで再作成してください。\n' + ''.join(patch))
            results[profile] = {'outcome': 'projected', 'changes': changes, 'patch': str(patch_path)}
        except (OSError, ValueError) as error:
            results[profile] = {'outcome': 'blocked', 'reason': str(error)}
    # Re-read only the selected settings and report if the evidence became stale.
    for name, digest in baselines.items():
        text = _read_only(Path(name))
        observed = None if text is None else hashlib.sha256(text.encode()).hexdigest()
        if observed != digest:
            raise ValueError('差分作成中に設定が変わりました。適用せず再確認してください')
    report = {
        'outcome': 'projected', 'read_home': str(live_home), 'profiles': results,
        'live_settings_changed': False, 'source_candidate': source,
        'ready_to_apply': False,
        'deferred': ['ai_knowledge planによる導入計画の再作成', 'GitHub上の共有内容と認証の確認',
                     'もう一台の現状確認', 'OS自動起動とAI側読み込みの確認'],
    }
    write_atomic(evidence / 'summary.json', encoded(report))
    lines = ['# 実環境への導入差分（未適用）', '',
             f'読取対象: `{live_home}`。この1台の現状に対する個人用・仕事用の2案。もう一台の差分ではありません。', '',
             '既存設定は読み取るだけで、実際の設定変更は行っていません。差分と設定のコピーはGit対象外のこのフォルダだけに保存しています。', '',
             '**この旧試作形式の差分は適用できるパッチではありません。** 実際の導入にはai_knowledge planでランタイムを含む差分を作成してください。', '',
             '受信元候補はローカルorigin/ブランチから作成。GitHubへ照会していません。差分に使う共有内容は模擬受信したサンプルです。', '',
             '| 想定 | 結果 | 変更対象数 |', '| --- | --- | --- |']
    for profile, result in results.items():
        lines.append(f"| {profile} | {result['outcome']} | {len(result.get('changes', []))} |")
        if result['outcome'] == 'blocked':
            lines.extend(['', profile + ': ' + result['reason']])
        else:
            lines.extend(['', f"[{profile}の追加・変更差分](evidence/{profile}.proposed.patch)", ''])
            lines.extend('- `' + item['path'] + '`' for item in result['changes'])
    lines.extend(['', '既存の指示・Hook・他の設定を保持する差分です。JSONの整形差分が含まれる場合があります。', '',
                  '本番ランタイム、バックアップと管理台帳、受信用Gitキャッシュも導入時に新設します。実際の受信元URL・ブランチ・共有内容を確定した後に差分を再生成し、確認を得てから設定します。', ''])
    write_atomic(root / 'report.md', '\n'.join(lines))
    report['report'] = str(root / 'report.md')
    return report
