"""Exercise the public command grammar across separate processes only."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prototype.fixtures import make_bundle


REPOSITORY = Path(__file__).absolute().parents[1]


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob('*') if path.is_file()
    }


class PrototypeCommandTests(unittest.TestCase):
    def cli(self, *arguments, exit_code=0):
        completed = subprocess.run(
            [sys.executable, '-B', '-m', 'prototype', *map(str, arguments)],
            capture_output=True, text=True, cwd=REPOSITORY, timeout=15,
        )
        self.assertEqual(completed.returncode, exit_code, completed.stderr + completed.stdout)
        self.assertEqual(completed.stderr, '')
        return json.loads(completed.stdout)

    def test_work_mac_commands_persist_state_and_default_to_preview(self):
        initialized = self.cli('init')
        self.assertEqual(initialized['outcome'], 'created')
        root = Path(initialized['sandbox'])
        home = root / 'work'
        arguments = ('--sandbox', root, '--pc', 'work')
        result = self.cli(*arguments, 'sync', '--at', '2026-10-20T10:00:00+09:00')
        self.assertEqual(result['outcome'], 'updated')
        state = self.cli(*arguments, 'status')
        self.assertEqual(state['active']['bundle']['version'], 'v1')
        self.assertEqual(self.cli(*arguments, 'pause-hooks')['outcome'], 'changed')

        (root / 'source/bundle.json').write_text(
            json.dumps(make_bundle('v2'), ensure_ascii=False), encoding='utf-8',
        )
        result = self.cli(*arguments, 'sync', '--at', '2026-10-20T11:00:00+09:00', '--force')
        self.assertEqual(result['outcome'], 'updated')
        state = self.cli(*arguments, 'status')
        self.assertEqual(state['active']['bundle']['version'], 'v2')
        self.assertIs(state['hooks_enabled'], False)

        before = snapshot(home)
        self.assertEqual(self.cli(*arguments, 'restore')['outcome'], 'preview')
        self.assertEqual(snapshot(home), before)
        self.assertEqual(self.cli(*arguments, 'status'), state)
        self.assertEqual(self.cli(*arguments, 'restore', '--apply')['outcome'], 'restored')
        restored = self.cli(*arguments, 'status')
        self.assertEqual(restored['active']['bundle']['version'], 'v1')
        self.assertIs(restored['sync_enabled'], False)
        self.assertIs(restored['hooks_enabled'], False)

        before = snapshot(home)
        self.assertEqual(self.cli(*arguments, 'uninstall')['outcome'], 'preview')
        self.assertEqual(snapshot(home), before)
        self.assertEqual(self.cli(*arguments, 'status'), restored)
        self.assertEqual(self.cli(*arguments, 'uninstall', '--apply')['outcome'], 'uninstalled')
        final = self.cli(*arguments, 'status')
        self.assertEqual(final['phase'], 'uninstalled')
        self.assertIsNone(final['active'])

    def test_unmarked_and_outside_sandboxes_are_rejected_without_creating_files(self):
        parent = REPOSITORY / '.prototype'
        parent.mkdir(exist_ok=True)
        unmarked = Path(tempfile.mkdtemp(prefix='cli-unmarked-', dir=parent))
        outside = Path(tempfile.mkdtemp(prefix='ai-knowledge-cli-outside-'))
        (outside / '.ai-knowledge-sandbox.json').write_text('{"schema": 1}\n')
        for root in (unmarked, outside):
            with self.subTest(sandbox=root):
                (root / 'personal').mkdir()
                (root / 'keep-local.txt').write_text('keep unchanged\n')
                before = snapshot(root)
                paths_before = set(root.rglob('*'))
                result = self.cli('--sandbox', root, '--pc', 'personal', 'sync', '--force', exit_code=1)
                self.assertEqual(result['outcome'], 'failed')
                self.assertEqual(snapshot(root), before)
                self.assertEqual(set(root.rglob('*')), paths_before)


if __name__ == '__main__':
    unittest.main()
