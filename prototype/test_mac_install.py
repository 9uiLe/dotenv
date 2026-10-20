"""Mac installation rehearsals use mock homes, local Git, and a fake OS only."""

from datetime import datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest
from unittest.mock import patch

from ai_knowledge.scheduler import FakeLaunchctl, Scheduler
from ai_knowledge.target import Target
from prototype.git_fixtures import LocalRemote
from prototype.sandbox import create_sandbox


JST = timezone(timedelta(hours=9))
RULES = {'codex': '.codex/AGENTS.md', 'claude': '.claude/CLAUDE.md'}
SETTINGS = {'codex': '.codex/hooks.json', 'claude': '.claude/settings.json'}


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def files_under(home, *, include_private=True):
    return {str(path.relative_to(home)): path.read_bytes()
            for path in home.rglob('*') if path.is_file()
            and (include_private or path.relative_to(home).parts[0] != '.local')}


class MacInstallationFixture:
    """模擬PCの操作だけを共有し、テストとしては探索されない。"""

    def setUp(self):
        self.root = create_sandbox(prefix='mac-install-test-')
        self.remote = LocalRemote(self.root)
        self.first = self.remote.publish('v1')
        self.config = {'url': str(self.remote.remote), 'branch': 'main',
                       'manifest': 'shared/manifest.json'}

    def target(self, profile='personal'):
        return Target.mock(self.root / profile)

    def local_settings(self, target):
        for tool in target.tools:
            write_text(target.home / RULES[tool], f'{tool}: existing local rules\n')
            write_text(target.home / SETTINGS[tool], json.dumps({
                'localSetting': f'keep-{tool}',
                'hooks': {'SessionStart': [{'hooks': [
                    {'type': 'command', 'command': f'echo local-{tool}'}]}]},
            }))
            os.chmod(target.home / RULES[tool], 0o640)
        write_text(target.home / '.agents/skills/local-only/SKILL.md', 'existing local skill\n')

    def state(self, target):
        from ai_knowledge.runtime import LiveManager
        return LiveManager(target).status()

    def create(self, target):
        from ai_knowledge.installer import create_plan
        result = create_plan(target, self.config)
        self.assertEqual(result['outcome'], 'preview', result)
        self.assertTrue(Path(result['plan']).is_absolute())
        self.assertTrue(Path(result['plan']).is_file())
        self.assertTrue(result['changes'])
        return result

    def install(self, target, *, scheduler=None):
        from ai_knowledge.installer import apply_plan
        plan = self.create(target)
        result = apply_plan(plan['plan'], scheduler=scheduler)
        self.assertEqual(result['outcome'], 'installed', result)
        return result

    def operate(self, target, command, **kwargs):
        from ai_knowledge.cli import operate
        return operate(target, command, **kwargs)

    def run_installed(self, target, *arguments, input_text=None, exit_code=0):
        environment = {key: value for key, value in os.environ.items()
                       if key not in {'PYTHONPATH', 'PYTHONHOME'}}
        completed = subprocess.run(
            [sys.executable, '-B', str(target.runtime / 'ai_knowledge/entrypoint.py'),
             *arguments], cwd=self.root / 'remote.git', env=environment,
            input=input_text, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, exit_code,
                         completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, '')
        return json.loads(completed.stdout)

    def next_morning(self, target):
        installed = datetime.fromisoformat(self.state(target)['last_success_date']).date()
        return datetime.combine(installed + timedelta(days=1), time(10), JST)

    def assert_local_settings(self, target):
        for tool in target.tools:
            self.assertIn(f'{tool}: existing local rules\n',
                          (target.home / RULES[tool]).read_text())
            self.assertEqual((target.home / RULES[tool]).stat().st_mode & 0o777, 0o640)
            settings = json.loads((target.home / SETTINGS[tool]).read_text())
            self.assertEqual(settings['localSetting'], f'keep-{tool}')
            self.assertIn({'hooks': [{'type': 'command', 'command': f'echo local-{tool}'}]},
                          settings['hooks']['SessionStart'])
        self.assertEqual((target.home / '.agents/skills/local-only/SKILL.md').read_text(),
                         'existing local skill\n')

class MacInstallationTests(MacInstallationFixture, unittest.TestCase):
    def test_plans_leave_both_mock_homes_untouched_and_installed_runtime_is_independent(self):
        from ai_knowledge.installer import apply_plan
        for profile in ('personal', 'work'):
            with self.subTest(profile=profile):
                target = self.target(profile)
                self.local_settings(target)
                before = files_under(target.home)
                before_paths = set(target.home.rglob('*'))
                plan = self.create(target)
                self.assertEqual(files_under(target.home), before)
                self.assertEqual(set(target.home.rglob('*')), before_paths)
                result = apply_plan(plan['plan'])
                self.assertEqual(result['outcome'], 'installed', result)
                self.assert_local_settings(target)
                self.assertEqual(self.state(target)['active']['source']['commit'], self.first)
                with (target.home / 'Library/LaunchAgents/local.ai-knowledge.sync.plist').open('rb') as stream:
                    schedule = plistlib.load(stream)
                self.assertEqual(schedule['StartCalendarInterval'], {'Hour': 10, 'Minute': 0})
                self.assertIs(schedule['RunAtLoad'], True)
                self.assertEqual(schedule['ProgramArguments'][-1], 'sync')
                self.assertEqual(schedule['WorkingDirectory'], str(target.home))
                self.assertTrue((target.runtime / 'ai_knowledge/entrypoint.py').is_file())
                self.assertTrue(self.run_installed(target, 'status')['scheduler']['registered'])
                self.run_installed(target, 'pause-sync')
                paused = self.run_installed(target, 'status')
                self.assertFalse(paused['sync_enabled'])
                self.assertFalse(paused['scheduler']['registered'])
                self.assertTrue(paused['hooks_enabled'])
                self.run_installed(target, 'resume-sync')
                self.assertTrue(self.run_installed(target, 'status')['scheduler']['registered'])
                for tool in target.tools:
                    output = self.run_installed(
                        target, 'hook', '--tool', tool, '--hook', 'shared-version',
                        input_text=json.dumps({'hook_event_name': 'SessionStart'}),
                    )
                    self.assertEqual(output['hookSpecificOutput']['additionalContext'], '共有設定 v1')
        self.assertFalse((self.root / 'personal/.claude').exists())


    def test_changed_settings_or_source_after_review_block_application(self):
        from ai_knowledge.installer import apply_plan
        for change in ('settings', 'source'):
            with self.subTest(change=change):
                target = self.target('personal' if change == 'settings' else 'work')
                self.local_settings(target)
                plan = self.create(target)
                if change == 'settings':
                    write_text(target.home / '.codex/AGENTS.md', 'edited after preview\n')
                else:
                    self.remote.publish('v2')
                before = files_under(target.home)
                with self.assertRaisesRegex(ValueError, '差分を再作成'):
                    apply_plan(plan['plan'])
                self.assertEqual(files_under(target.home), before)


    def test_failed_os_registration_keeps_shared_files_available_but_pauses_automatic_updates(self):
        from ai_knowledge.installer import apply_plan
        target = self.target()
        plan = self.create(target)
        fake = FakeLaunchctl(failures={'bootstrap': 5})
        result = apply_plan(plan['plan'], scheduler=Scheduler(target.home, runner=fake, uid=501))
        self.assertEqual(result['outcome'], 'failed', result)
        self.assertFalse(fake.registered)
        self.assertFalse(self.state(target)['sync_enabled'])
        self.assertEqual(self.state(target)['active']['bundle']['version'], 'v1')
        self.assertFalse((target.home / 'Library/LaunchAgents/local.ai-knowledge.sync.plist').exists())


    def test_setting_changed_immediately_before_the_install_transaction_is_not_overwritten(self):
        from ai_knowledge import installer
        target = self.target()
        self.local_settings(target)
        plan = self.create(target)
        original_sync = installer._InstallManager.sync

        def edit_then_sync(manager, *arguments, **options):
            write_text(target.home / '.codex/AGENTS.md', 'edited as installation began\n')
            return original_sync(manager, *arguments, **options)

        with patch.object(installer._InstallManager, 'sync', edit_then_sync):
            result = installer.apply_plan(plan['plan'])
        self.assertEqual(result['outcome'], 'failed', result)
        self.assertEqual((target.home / '.codex/AGENTS.md').read_text(),
                         'edited as installation began\n')
        self.assertFalse((target.home / '.agents/skills/ai-knowledge-example/SKILL.md').exists())


    def test_existing_or_unreadable_os_registration_is_preserved_without_installing(self):
        from ai_knowledge.installer import apply_plan
        target = self.target()
        self.local_settings(target)
        plan = self.create(target)
        before = files_under(target.home)
        for fake in (FakeLaunchctl(registered=True), FakeLaunchctl(failures={'print': 5})):
            with self.subTest(registered=fake.registered):
                scheduler = Scheduler(target.home, runner=fake, uid=501)
                with self.assertRaisesRegex(ValueError, '既に登録'):
                    apply_plan(plan['plan'], scheduler=scheduler)
                self.assertEqual(files_under(target.home), before)
                self.assertTrue(all(call[1] == 'print' for call in fake.calls))


    def test_private_plan_rejects_shared_permissions_fifo_and_links_without_changing_home(self):
        from ai_knowledge.installer import apply_plan
        target = self.target()
        self.local_settings(target)
        plan = Path(self.create(target)['plan'])
        before, paths = files_under(target.home), set(target.home.rglob('*'))
        saved = plan.with_name('saved-plan.json')
        for kind in ('shared-file', 'fifo', 'link', 'shared-folder'):
            with self.subTest(kind=kind):
                try:
                    if kind == 'shared-file':
                        os.chmod(plan, 0o644)
                    elif kind == 'shared-folder':
                        # The generated sandbox stays private; test rejection of a shared plan directory.
                        os.chmod(plan.parent, 0o755)  # nosec B103
                    else:
                        plan.rename(saved)
                        if kind == 'fifo':
                            os.mkfifo(plan, 0o600)
                        else:
                            plan.symlink_to(saved)
                    with self.assertRaises(ValueError):
                        apply_plan(plan)
                    self.assertEqual(files_under(target.home), before)
                    self.assertEqual(set(target.home.rglob('*')), paths)
                finally:
                    os.chmod(plan.parent, 0o700)
                    if saved.exists():
                        plan.unlink()
                        saved.rename(plan)
                    os.chmod(plan, 0o600)


    def test_uninstalled_commands_do_not_create_state_or_register_an_agent(self):
        target = self.target()
        self.local_settings(target)
        before, paths = files_under(target.home), set(target.home.rglob('*'))
        fake = FakeLaunchctl()
        scheduler = Scheduler(target.home, runner=fake, uid=501)
        self.assertEqual(self.operate(target, 'status', scheduler=scheduler),
                         {'outcome': 'not_installed'})
        for command in ('sync', 'pause-sync', 'resume-sync', 'pause-hooks',
                        'resume-hooks', 'restore', 'uninstall'):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, '初回導入'):
                    self.operate(target, command, scheduler=scheduler)
                self.assertEqual(files_under(target.home), before)
                self.assertEqual(set(target.home.rglob('*')), paths)
        self.assertFalse(target.data.exists())
        self.assertEqual(fake.calls, [])


    def test_changed_installed_runtime_blocks_operations_and_hooks(self):
        target = self.target()
        self.install(target)
        runtime_file = target.runtime / 'prototype/engine.py'
        write_text(runtime_file, runtime_file.read_text() + '\n# changed after installation\n')
        before = files_under(target.home)
        fake = FakeLaunchctl(registered=True)
        scheduler = Scheduler(target.home, runner=fake, uid=501)
        for command in ('status', 'sync', 'pause-sync', 'resume-sync', 'pause-hooks',
                        'resume-hooks', 'restore', 'uninstall'):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, '実行処理が変更'):
                    self.operate(target, command, scheduler=scheduler)
                self.assertEqual(files_under(target.home), before)
        result = self.run_installed(target, 'hook', '--tool', 'codex',
                                    '--hook', 'shared-version', input_text='{}', exit_code=1)
        self.assertEqual(result['outcome'], 'failed')
        self.assertNotIn('hookSpecificOutput', result)
        self.assertEqual(files_under(target.home), before)
        self.assertEqual(fake.calls, [])



if __name__ == '__main__':
    unittest.main()
