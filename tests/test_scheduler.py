"""OS registration behavior through the fake adapter; no real launchctl calls."""

from pathlib import Path
import subprocess
import shutil
import unittest
from unittest.mock import patch

from ai_knowledge.scheduler import FakeLaunchctl, LABEL, PLIST, Scheduler
from ai_knowledge.sandbox import create_sandbox


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix='scheduler-test-')
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / 'personal'
        self.plist = self.home / PLIST
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text('prepared by the distribution manager\n', encoding='utf-8')
        self.adapter = FakeLaunchctl()
        self.scheduler = Scheduler(self.home, runner=self.adapter, uid=501)
        self.no_process = patch(
            'ai_knowledge.scheduler.subprocess.run',
            side_effect=AssertionError('scheduler tests must never invoke a real process'),
        )
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def test_register_stop_resume_and_already_stopped_use_only_the_dedicated_agent(self):
        self.assertFalse(self.scheduler.status()['registered'])
        self.assertEqual(self.scheduler.register()['outcome'], 'registered')
        self.assertTrue(self.adapter.registered)
        self.assertFalse(self.scheduler.register()['changed'])
        self.assertTrue(self.scheduler.unregister()['changed'])
        self.assertFalse(self.adapter.registered)
        self.assertFalse(self.scheduler.unregister()['changed'])
        self.assertEqual(self.scheduler.register()['outcome'], 'registered')

        mutations = [call for call in self.adapter.calls if call[1] != 'print']
        self.assertEqual(mutations, [
            ['/bin/launchctl', 'bootstrap', 'gui/501', str(self.plist)],
            ['/bin/launchctl', 'bootout', f'gui/501/{LABEL}'],
            ['/bin/launchctl', 'bootstrap', 'gui/501', str(self.plist)],
        ])
        for options in self.adapter.options:
            self.assertIs(options['shell'], False)
            self.assertEqual(options['timeout'], 15)
            self.assertEqual(options['env'], {
                'HOME': str(self.home), 'PATH': '/usr/bin:/bin',
                'LANG': 'C', 'LC_ALL': 'C',
            })

    def test_bootstrap_failure_is_reported_without_claiming_registration(self):
        self.adapter.failures['bootstrap'] = 5
        result = self.scheduler.register()
        self.assertEqual(result['outcome'], 'failed')
        self.assertFalse(self.adapter.registered)
        self.assertNotIn('registered', result)

    def test_unknown_status_is_a_failure_and_never_a_reason_to_remove_the_agent(self):
        self.adapter.failures['print'] = 5
        self.assertEqual(self.scheduler.unregister()['outcome'], 'failed')
        self.assertEqual(self.scheduler.register()['outcome'], 'failed')
        self.assertTrue(all(call[1] == 'print' for call in self.adapter.calls))

    def test_failed_stop_reports_failure_and_keeps_registration_observable(self):
        self.adapter.registered = True
        self.adapter.failures['bootout'] = 5
        self.assertEqual(self.scheduler.unregister()['outcome'], 'failed')
        self.assertTrue(self.scheduler.status()['registered'])

    def test_timeout_and_process_errors_do_not_expose_os_output(self):
        secret = 'private launchctl output'
        self.adapter.failures['print'] = subprocess.TimeoutExpired(
            ['launchctl'], 15, output=secret, stderr=secret,
        )
        result = self.scheduler.status()
        self.assertEqual(result['outcome'], 'failed')
        self.assertNotIn(secret, str(result))
        self.adapter.failures['print'] = OSError(secret)
        self.assertEqual(self.scheduler.status()['outcome'], 'failed')
        self.assertNotIn(secret, str(self.scheduler.status()))

    def test_missing_or_linked_plist_is_not_registered(self):
        self.plist.unlink()
        self.assertEqual(self.scheduler.register()['outcome'], 'failed')
        alternative = self.home / 'other.plist'
        alternative.write_text('other setting', encoding='utf-8')
        self.plist.symlink_to(alternative)
        self.assertEqual(self.scheduler.register()['outcome'], 'failed')
        self.assertTrue(all(call[1] == 'print' for call in self.adapter.calls))

    def test_mock_home_requires_an_explicit_fake_os_adapter(self):
        with self.assertRaises(ValueError):
            Scheduler(self.home)
        with self.assertRaises(ValueError):
            Scheduler(self.home, runner=self.adapter, uid=0)
        with self.assertRaises(ValueError):
            Scheduler(Path('relative-home'), runner=self.adapter, uid=501)

    def test_linked_launchagents_directory_is_not_registered(self):
        original = self.home / 'prepared-agents'
        self.plist.parent.rename(original)
        self.plist.parent.symlink_to(original, target_is_directory=True)
        self.assertEqual(self.scheduler.register()['outcome'], 'failed')
        self.assertFalse(self.adapter.registered)


if __name__ == '__main__':
    unittest.main()
