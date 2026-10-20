"""模擬PCで日次更新・停止・復元・再導入の継続動作を確認する。"""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from ai_knowledge.scheduler import FakeLaunchctl, Scheduler
from prototype.test_mac_install import MacInstallationFixture, RULES, SETTINGS, files_under


class MacLifecycleTests(MacInstallationFixture, unittest.TestCase):
    def test_calendar_boundary_once_a_day_and_late_login_catchup_survive_restart(self):
        target = self.target()
        self.install(target)
        morning = self.next_morning(target)
        self.remote.publish('v2')
        self.assertEqual(self.operate(target, 'sync', at=morning - timedelta(minutes=1))['outcome'],
                         'not_due')
        self.assertEqual(self.state(target)['active']['bundle']['version'], 'v1')
        self.assertEqual(self.operate(target, 'sync', at=morning)['outcome'], 'updated')
        self.assertEqual(self.operate(target, 'sync', at=morning + timedelta(hours=1))['outcome'],
                         'already_today')
        self.remote.publish('v3')
        self.assertEqual(self.run_installed(
            target, 'sync', '--at', (morning + timedelta(days=1, hours=3)).isoformat(),
        )['outcome'], 'updated')
        self.assertEqual(self.state(target)['active']['bundle']['version'], 'v3')
        self.run_installed(target, 'status')
        self.assertEqual(self.state(target)['active']['bundle']['version'], 'v3')


    def test_pausing_sync_and_hooks_are_independent_and_repeated_stop_is_safe(self):
        target = self.target('work')
        fake = FakeLaunchctl()
        scheduler = Scheduler(target.home, runner=fake, uid=501)
        self.install(target, scheduler=scheduler)
        self.assertTrue(fake.registered)
        self.assertEqual(self.operate(target, 'pause-sync', scheduler=scheduler)['outcome'], 'changed')
        self.assertFalse(fake.registered)
        self.assertFalse(self.state(target)['sync_enabled'])
        self.assertTrue(self.state(target)['hooks_enabled'])
        self.operate(target, 'pause-sync', scheduler=scheduler)
        self.assertFalse(fake.registered)
        self.assertEqual(self.operate(target, 'sync', at=self.next_morning(target))['outcome'], 'paused')
        output = self.run_installed(target, 'hook', '--tool', 'codex', '--hook', 'shared-version',
                                    input_text='{}')
        self.assertEqual(output['hookSpecificOutput']['additionalContext'], '共有設定 v1')
        self.operate(target, 'pause-hooks', scheduler=scheduler)
        self.assertEqual(self.run_installed(target, 'hook', '--tool', 'codex',
                                            '--hook', 'shared-version', input_text='{}'), {})
        self.operate(target, 'resume-sync', scheduler=scheduler)
        self.assertTrue(fake.registered)
        self.assertTrue(self.state(target)['sync_enabled'])
        self.assertFalse(self.state(target)['hooks_enabled'])
        self.operate(target, 'resume-hooks', scheduler=scheduler)
        self.assertTrue(self.state(target)['hooks_enabled'])


    def test_restore_preview_then_restore_and_uninstall_keep_existing_configuration(self):
        target = self.target('work')
        self.local_settings(target)
        fake = FakeLaunchctl()
        scheduler = Scheduler(target.home, runner=fake, uid=501)
        self.install(target, scheduler=scheduler)
        self.remote.publish('v2')
        self.assertEqual(self.operate(target, 'sync', at=self.next_morning(target))['outcome'], 'updated')
        before = files_under(target.home)
        self.assertEqual(self.operate(target, 'restore', scheduler=scheduler)['outcome'], 'preview')
        self.assertEqual(files_under(target.home), before)
        self.assertTrue(fake.registered)
        result = self.operate(target, 'restore', apply=True, scheduler=scheduler)
        self.assertEqual(result['outcome'], 'restored', result)
        self.assertFalse(fake.registered)
        state = self.state(target)
        self.assertFalse(state['sync_enabled'])
        self.assertFalse(state['hooks_enabled'])
        self.assertEqual(state['active']['bundle']['version'], 'v1')
        self.assertEqual(self.run_installed(target, 'hook', '--tool', 'claude',
                                            '--hook', 'shared-version', input_text='{}'), {})
        self.assert_local_settings(target)
        before = files_under(target.home)
        self.assertEqual(self.operate(target, 'uninstall', scheduler=scheduler)['outcome'], 'preview')
        self.assertEqual(files_under(target.home), before)
        result = self.operate(target, 'uninstall', apply=True, scheduler=scheduler)
        self.assertEqual(result['outcome'], 'uninstalled', result)
        self.assertFalse(fake.registered)
        self.assert_local_settings(target)
        for tool in target.tools:
            self.assertEqual((target.home / RULES[tool]).read_text(), f'{tool}: existing local rules\n')
            self.assertNotIn('ai-knowledge:begin', (target.home / RULES[tool]).read_text())
        self.assertFalse((target.home / '.agents/skills/ai-knowledge-example/SKILL.md').exists())


    def test_reinstall_after_initial_settings_restore_receives_latest_and_keeps_hooks_paused(self):
        target = self.target('personal')
        self.local_settings(target)
        baseline_rules = (target.home / RULES['codex']).read_text()
        baseline_hooks = json.loads((target.home / SETTINGS['codex']).read_text())
        fake = FakeLaunchctl()
        scheduler = Scheduler(target.home, runner=fake, uid=501)
        self.install(target, scheduler=scheduler)
        self.assertEqual(self.state(target)['history'], [])
        installed = files_under(target.home)
        with self.assertRaises(ValueError):
            self.operate(target, 'reinstall', apply=True, scheduler=scheduler)
        self.assertEqual(files_under(target.home), installed)

        result = self.operate(target, 'uninstall', apply=True, scheduler=scheduler)
        self.assertEqual(result['outcome'], 'uninstalled', result)
        self.assertFalse(fake.registered)
        self.assert_local_settings(target)
        self.assertEqual((target.home / RULES['codex']).read_text(), baseline_rules)
        self.assertEqual(json.loads((target.home / SETTINGS['codex']).read_text()), baseline_hooks)
        self.assertFalse((target.home / '.agents/skills/ai-knowledge-example/SKILL.md').exists())
        with self.assertRaises(ValueError):
            self.operate(target, 'resume-sync', scheduler=scheduler)

        second = self.remote.publish('v2')
        before_preview = files_under(target.home, include_private=False)
        before_state = self.state(target)
        preview = self.operate(target, 'reinstall', scheduler=scheduler)
        self.assertEqual(preview['outcome'], 'preview', preview)
        self.assertEqual(files_under(target.home, include_private=False), before_preview)
        self.assertEqual(self.state(target), before_state)
        self.assertFalse(fake.registered)

        result = self.operate(target, 'reinstall', apply=True, scheduler=scheduler)
        self.assertEqual(result['outcome'], 'reinstalled', result)
        state = self.state(target)
        self.assertEqual(state['phase'], 'ready')
        self.assertEqual(state['active']['source']['commit'], second)
        self.assertEqual(state['active']['bundle']['version'], 'v2')
        self.assertTrue(state['sync_enabled'])
        self.assertFalse(state['hooks_enabled'])
        self.assertTrue(fake.registered)
        self.assert_local_settings(target)
        self.assertEqual(self.run_installed(target, 'hook', '--tool', 'codex',
                                            '--hook', 'shared-version', input_text='{}'), {})
        self.operate(target, 'resume-hooks', scheduler=scheduler)
        output = self.run_installed(target, 'hook', '--tool', 'codex',
                                    '--hook', 'shared-version', input_text='{}')
        self.assertEqual(output['hookSpecificOutput']['additionalContext'], '共有設定 v2')


    def test_registration_and_pause_failures_report_unconfirmed_stop_on_install_and_resume(self):
        from ai_knowledge.installer import apply_plan
        from ai_knowledge.runtime import LiveManager
        failure = {'outcome': 'failed', 'reason': 'injected operation lock conflict'}
        for action in ('install', 'resume'):
            with self.subTest(action=action):
                target = self.target('personal' if action == 'install' else 'work')
                if action == 'install':
                    plan = self.create(target)
                else:
                    self.install(target)
                    self.operate(target, 'pause-sync')
                fake = FakeLaunchctl(failures={'bootstrap': 5})
                scheduler = Scheduler(target.home, runner=fake, uid=501)
                with patch.object(LiveManager, 'pause_sync', return_value=failure):
                    result = (apply_plan(plan['plan'], scheduler=scheduler) if action == 'install' else
                              self.operate(target, 'resume-sync', scheduler=scheduler))
                self.assertEqual(result['outcome'], 'failed', result)
                self.assertIs(result['sync_stop_confirmed'], False)
                self.assertEqual(result['pause_result'], failure)
                self.assertIn('停止を確認できません', result['reason'])
                self.assertNotIn('停止しました', result['reason'])
                self.assertTrue(self.state(target)['sync_enabled'])
                self.assertFalse(fake.registered)



if __name__ == '__main__':
    unittest.main()
