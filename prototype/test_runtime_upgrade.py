"""旧版の照合情報を持つ模擬PCで、実行処理だけの更新を確認する。"""

from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from ai_knowledge.installer import runtime_files
from ai_knowledge.target import Target
from ai_knowledge.upgrade import apply_upgrade_plan, create_upgrade_plan
from prototype.engine import checksum, encoded, write_atomic
from prototype.sandbox import create_sandbox


class RuntimeUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox('runtime-upgrade-test-')
        self.addCleanup(shutil.rmtree, self.root)
        self.target = Target.mock(self.root / 'personal')
        self.files = runtime_files()
        self.new_name = ('ai_knowledge/upgrade.py' if 'ai_knowledge/upgrade.py' in self.files
                         else 'ai_knowledge/scheduler.py')
        old_files = {name: text for name, text in self.files.items() if name != self.new_name}
        # A changed module plus a missing module exercise both upgrade operations.
        old_files['ai_knowledge/__init__.py'] = '# 導入時の実行処理\n'
        self.target.data.mkdir(parents=True, mode=0o700)
        for name, text in old_files.items():
            write_atomic(self.target.runtime / name, text)
        self.installation = {'schema': 1, 'home': str(self.target.home),
                             'profile': self.target.profile, 'mode': self.target.mode,
                             'python': str(self.target.python), 'custom': {'keep': True},
                             'runtime_hashes': {name: checksum(text) for name, text in old_files.items()}}
        write_atomic(self.target.data / 'installation.json', encoded(self.installation))
        self.state = {'schema': 1, 'phase': 'ready', 'active': {'version': 'previous'},
                      'history': ['preserve'], 'sync_enabled': False, 'hooks_enabled': False}
        write_atomic(self.target.data / 'state.json', encoded(self.state))
        write_atomic(self.target.data / 'operation.lock', '')
        write_atomic(self.target.source / 'git.json', encoded({'url': 'keep-source'}))
        write_atomic(self.target.home / '.codex/AGENTS.md', '既存の共有設定\n')

    def snapshot(self):
        return {str(path.relative_to(self.target.home)): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in self.target.home.rglob('*') if path.is_file()
                and 'backups' not in path.relative_to(self.target.home).parts}

    def test_preview_leaves_home_untouched_and_apply_keeps_settings_and_state(self):
        before = self.snapshot()
        preview = create_upgrade_plan(self.target)
        self.addCleanup(shutil.rmtree, Path(preview['plan']).parent)
        self.assertEqual(preview['outcome'], 'preview')
        self.assertEqual(preview['action'], 'runtime-upgrade')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Path(preview['plan']).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(preview['plan']).parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(self.new_name, Path(preview['patch']).read_text())

        result = apply_upgrade_plan(preview['plan'])
        self.assertEqual(result['outcome'], 'upgraded')
        self.assertGreater(result['changed_files'], 1)
        expected = deepcopy(self.installation)
        expected['runtime_hashes'] = {name: checksum(text) for name, text in self.files.items()}
        self.assertEqual(json.loads((self.target.data / 'installation.json').read_text()), expected)
        for name, text in self.files.items():
            self.assertEqual((self.target.runtime / name).read_text(), text)
        self.assertEqual((self.target.data / 'state.json').read_bytes(),
                         before['.local/share/ai-knowledge/state.json'][0])
        self.assertEqual((self.target.source / 'git.json').read_bytes(),
                         before['.local/share/ai-knowledge/source/git.json'][0])
        self.assertEqual((self.target.home / '.codex/AGENTS.md').read_bytes(),
                         before['.codex/AGENTS.md'][0])
        backup = Path(result['backup']) / 'before.json'
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)

    def test_old_hash_list_detects_modified_runtime_before_preview(self):
        write_atomic(self.target.runtime / 'ai_knowledge/__init__.py', '# 勝手に変更\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '実行処理が変更'):
            create_upgrade_plan(self.target)
        self.assertEqual(self.snapshot(), before)

    def test_reviewed_runtime_state_and_installer_sources_must_still_match(self):
        for change in ('runtime', 'state', 'installation', 'source'):
            with self.subTest(change=change):
                preview = create_upgrade_plan(self.target)
                self.addCleanup(shutil.rmtree, Path(preview['plan']).parent)
                path = {'runtime': self.target.runtime / 'ai_knowledge/__init__.py',
                        'state': self.target.data / 'state.json',
                        'installation': self.target.data / 'installation.json'}.get(change)
                saved = path.read_bytes() if path else None
                files = dict(self.files)
                if change == 'source':
                    files['ai_knowledge/__init__.py'] += '\n# 原本の変更\n'
                else:
                    write_atomic(path, saved.decode() + '\n')
                before = self.snapshot()
                with patch('ai_knowledge.installer.runtime_files', return_value=files):
                    with self.assertRaisesRegex(ValueError, '変更|差分を再作成'):
                        apply_upgrade_plan(preview['plan'])
                self.assertEqual(self.snapshot(), before)
                if path:
                    write_atomic(path, saved.decode())

    def test_new_runtime_file_collision_is_preserved(self):
        collision = self.target.runtime / self.new_name
        write_atomic(collision, '# 個別のファイル\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '管理外'):
            create_upgrade_plan(self.target)
        self.assertEqual(self.snapshot(), before)

    def test_installation_identity_must_match_selected_target(self):
        for key, value in [('home', '/another/home'), ('profile', 'work'),
                           ('mode', 'live'), ('python', '/another/python')]:
            with self.subTest(key=key):
                changed = {**self.installation, key: value}
                write_atomic(self.target.data / 'installation.json', encoded(changed))
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, '一致しません'):
                    create_upgrade_plan(self.target)
                self.assertEqual(self.snapshot(), before)
        write_atomic(self.target.data / 'installation.json', encoded(self.installation))

    def test_sync_lock_prevents_preview_and_apply(self):
        preview = create_upgrade_plan(self.target)
        self.addCleanup(shutil.rmtree, Path(preview['plan']).parent)
        before = self.snapshot()
        with (self.target.data / 'operation.lock').open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, '実行中'):
                create_upgrade_plan(self.target)
            with self.assertRaisesRegex(ValueError, '実行中'):
                apply_upgrade_plan(preview['plan'])
        self.assertEqual(self.snapshot(), before)

    def test_write_failure_rolls_back_changes_and_keeps_private_backup(self):
        preview = create_upgrade_plan(self.target)
        self.addCleanup(shutil.rmtree, Path(preview['plan']).parent)
        before = self.snapshot()
        real_write = write_atomic
        calls = []

        def fail_once(path, text, mode=0o600):
            if Path(path).is_relative_to(self.target.runtime):
                calls.append(str(path))
                if len(calls) == 2:
                    raise OSError('書き込み障害')
            return real_write(path, text, mode)

        with patch('ai_knowledge.upgrade.write_atomic', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, '元の状態へ戻しました'):
                apply_upgrade_plan(preview['plan'])
        self.assertEqual(self.snapshot(), before)
        backups = list((self.target.data / 'backups').glob('runtime-upgrade-*/before.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
