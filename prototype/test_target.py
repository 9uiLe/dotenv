"""Installed-runtime boundaries verified against fabricated homes only."""

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_knowledge.runtime import LiveManager, run_hook
from ai_knowledge.source import CiGateError
from ai_knowledge.target import Target
from prototype.engine import Conflict, checksum, encoded, write_atomic
from prototype.fixtures import make_bundle
from prototype.sandbox import create_sandbox


CONFIG = {'url': 'https://github.com/example/knowledge', 'branch': 'master',
          'manifest': 'shared/manifest.json'}


class LiveTargetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox('target-test-')
        self.addCleanup(shutil.rmtree, self.root)
        self.home = self.root / 'personal'
        # A fabricated passwd home allows the live boundary to be exercised
        # without reading or writing this computer's actual AI configuration.
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        uid = os.getuid() or 501
        self.stack.enter_context(patch('ai_knowledge.target.platform.system', return_value='Darwin'))
        self.stack.enter_context(patch('ai_knowledge.target.os.getuid', return_value=uid))
        self.stack.enter_context(patch('ai_knowledge.target.os.geteuid', return_value=uid))
        self.stack.enter_context(patch('ai_knowledge.target.pwd.getpwuid',
                                       return_value=SimpleNamespace(pw_dir=str(self.home))))

    def private_data(self, target):
        target.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.data.chmod(0o700)

    def test_live_target_accepts_only_standard_home_nonprivileged_mac(self):
        self.assertEqual(Target.live('personal').home, self.home)
        with self.assertRaisesRegex(ValueError, '標準home'):
            Target.live('personal', self.root / 'work')
        for name, value in [('platform.system', 'Linux'), ('os.getuid', 0), ('os.geteuid', 0)]:
            with self.subTest(rejected=name), patch('ai_knowledge.target.' + name, return_value=value):
                with self.assertRaisesRegex(ValueError, '通常ユーザー'):
                    Target.live('personal')

    def test_managed_links_and_special_files_are_rejected_before_access(self):
        config = self.home / '.codex/config.toml'
        config.parent.mkdir()
        outside = self.root / 'unmanaged.txt'
        outside.write_text('unchanged\n')
        config.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'シンボリックリンク'):
            Target.live('personal')
        self.assertEqual(outside.read_text(), 'unchanged\n')
        config.unlink()
        os.mkfifo(config)
        with self.assertRaisesRegex(ValueError, '通常ファイル'):
            Target.live('personal')

    def test_unrelated_home_symlinks_do_not_prevent_installation(self):
        downloads = self.home / 'Downloads'
        downloads.mkdir()
        (downloads / 'link').symlink_to(self.root / 'missing')
        self.assertEqual(Target.live('personal').guard(), self.home)

    def test_hook_guard_rejects_changed_managed_path_before_script_execution(self):
        target = Target.live('personal')
        self.private_data(target)
        write_atomic(target.data / 'state.json', encoded({
            'phase': 'ready', 'hooks_enabled': False, 'active': None,
        }))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_hook(target, 'codex', 'sample'), 0)
        self.assertEqual(json.loads(output.getvalue()), {})
        state = target.data / 'state.json'
        state.unlink()
        state.symlink_to(self.root / 'outside-state.json')
        with patch('prototype.hook_runner.subprocess.run') as launch:
            with self.assertRaisesRegex(ValueError, 'シンボリックリンク'):
                run_hook(target, 'codex', 'sample')
            launch.assert_not_called()

    def test_override_rules_block_initial_application(self):
        target = Target.live('personal')
        override = target.home / '.codex/AGENTS.override.md'
        write_atomic(override, 'Existing rules take priority.\n')
        manager = LiveManager(target)
        with self.assertRaisesRegex(Conflict, 'AGENTS.override.md'):
            manager._plan(make_bundle('v1'), manager._default())
        self.assertFalse((self.home / '.codex/AGENTS.md').exists())

    def test_live_source_refuses_local_transport_without_starting_git(self):
        manager = LiveManager(Target.live('personal'))
        with patch('ai_knowledge.runtime.validate_config') as validate:
            with self.assertRaisesRegex(Conflict, 'HTTPS'):
                manager.validate_source({**CONFIG, 'url': str(self.root / 'remote.git')})
            validate.assert_not_called()

    def test_pending_ci_keeps_previous_shared_rules_skills_and_active_version(self):
        target = Target.live('personal')
        self.private_data(target)
        write_atomic(target.source / 'git.json', encoded(CONFIG))
        rules = self.home / '.codex/AGENTS.md'
        write_atomic(rules, 'My individual rules.\n')
        manager = LiveManager(target)
        first = {'bundle': make_bundle('v1'), 'source': {
            'kind': 'git', **CONFIG, 'commit': 'a' * 40,
        }}
        with patch('ai_knowledge.runtime.receive', return_value=first), \
                patch('ai_knowledge.source.require_ci_success', return_value={'commit': 'a' * 40}):
            self.assertEqual(manager.sync(force=True)['outcome'], 'updated')
        before_state = deepcopy(manager.status())
        before_releases = sorted(path.name for path in (target.data / 'releases').iterdir())
        paths = [rules, self.home / '.codex/hooks.json',
                 self.home / '.agents/skills/ai-knowledge-example/SKILL.md']
        before_files = {path: path.read_bytes() for path in paths}
        second = {'bundle': make_bundle('v2'), 'source': {
            'kind': 'git', **CONFIG, 'commit': 'b' * 40,
        }}
        with patch('ai_knowledge.runtime.receive', return_value=second), \
                patch('ai_knowledge.source.require_ci_success', side_effect=CiGateError('CI pending')):
            result = manager.sync(force=True)
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual({path: path.read_bytes() for path in paths}, before_files)
        after_state = manager.status()
        for key in ('active', 'history', 'last_received_source', 'last_success_date'):
            self.assertEqual(after_state[key], before_state[key])
        self.assertEqual(sorted(path.name for path in (target.data / 'releases').iterdir()), before_releases)

    def test_state_cannot_redirect_shared_writes_into_private_runtime(self):
        target = Target.live('personal')
        self.private_data(target)
        manager = LiveManager(target)
        state = manager._default()
        bundle = make_bundle('v1')
        state['active'] = {'bundle': bundle, 'revision': checksum(encoded(bundle)),
                           'managed': {'.local/share/ai-knowledge/installation.json': {
                               'kind': 'file', 'payload': 'redirected',
                           }}}
        write_atomic(manager.state_path, encoded(state))
        with self.assertRaisesRegex(Conflict, '設定対象外'):
            manager.status()

    def test_private_management_data_requires_owner_only_permissions(self):
        target = Target.live('personal')
        self.private_data(target)
        target.data.chmod(0o755)
        with self.assertRaisesRegex(ValueError, '700'):
            target.guard()
        target.data.chmod(0o700)
        other_uid = os.getuid() + 1
        with patch('ai_knowledge.target.os.getuid', return_value=other_uid), \
                patch('ai_knowledge.target.os.geteuid', return_value=other_uid):
            with self.assertRaisesRegex(ValueError, '本人所有'):
                target.guard()
        write_atomic(target.data / 'state.json', '{}\n', 0o644)
        with self.assertRaisesRegex(ValueError, '600'):
            target.guard()
        (target.data / 'state.json').chmod(0o600)
        # Published content is allowed to be 644 inside the private data root.
        write_atomic(target.data / 'releases/example/hooks/session.py', 'pass\n', 0o644)
        target.guard()

    def test_oversized_state_stops_update_and_hook_without_touching_settings(self):
        target = Target.live('personal')
        self.private_data(target)
        rules = self.home / '.codex/AGENTS.md'
        write_atomic(rules, 'Existing personal instructions.\n')
        with (target.data / 'state.json').open('wb') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        (target.data / 'state.json').chmod(0o600)
        manager = LiveManager(target)
        with patch('ai_knowledge.runtime.receive') as receive:
            result = manager.sync(force=True)
            receive.assert_not_called()
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('64MiB', result['reason'])
        self.assertEqual(rules.read_text(), 'Existing personal instructions.\n')
        with patch('prototype.hook_runner.subprocess.run') as launch:
            with self.assertRaisesRegex(ValueError, 'size limit'):
                run_hook(target, 'codex', 'sample')
            launch.assert_not_called()

    def test_update_that_would_exceed_state_limit_keeps_existing_settings(self):
        target = Target.live('personal')
        self.private_data(target)
        write_atomic(target.source / 'git.json', encoded(CONFIG))
        rules = self.home / '.codex/AGENTS.md'
        write_atomic(rules, 'Existing personal instructions.\n')
        received = {'bundle': make_bundle('v1'), 'source': {
            'kind': 'git', **CONFIG, 'commit': 'a' * 40,
        }}
        with patch('ai_knowledge.runtime.MAX_STATE_BYTES', 1024), \
                patch('ai_knowledge.runtime.receive', return_value=received), \
                patch('ai_knowledge.source.require_ci_success', return_value={}):
            result = LiveManager(target).sync(force=True)
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('更新後', result['reason'])
        self.assertEqual(rules.read_text(), 'Existing personal instructions.\n')
        self.assertFalse((target.data / 'releases').exists())
        self.assertIsNone(LiveManager(target).status()['active'])


if __name__ == '__main__':
    unittest.main()
