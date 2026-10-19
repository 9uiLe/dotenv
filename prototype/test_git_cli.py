"""Consumer checks for Git source configuration and preview command syntax."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from prototype.git_fixtures import LocalRemote


REPOSITORY = Path(__file__).absolute().parents[1]


def target_files(home):
    return {
        path.relative_to(home).as_posix(): path.read_bytes()
        for path in home.rglob('*')
        if path.is_file() and path.relative_to(home).parts[0] != '.local'
    }


class GitCommandTests(unittest.TestCase):
    def cli(self, *arguments, exit_code=0):
        completed = subprocess.run(
            [sys.executable, '-B', '-m', 'prototype', *map(str, arguments)],
            capture_output=True, text=True, timeout=20, cwd=REPOSITORY,
        )
        self.assertEqual(completed.returncode, exit_code, completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, '')
        return json.loads(completed.stdout)

    def test_configure_without_fetch_then_preview_and_daily_receive(self):
        root = Path(self.cli('init')['sandbox'])
        home = root / 'work'
        remote = LocalRemote(root)
        first = remote.publish('v1')
        arguments = ('--sandbox', root, '--pc', 'work')
        cache = home / '.local/share/ai-knowledge/receive-cache.git'
        self.assertFalse(cache.exists())
        configured = self.cli(*arguments, 'configure-git', '--url', remote.remote, '--branch', 'main')
        self.assertEqual(configured['outcome'], 'configured')
        self.assertIs(configured['fetched'], False)
        self.assertEqual(configured['source']['manifest'], 'shared/manifest.json')
        self.assertEqual(json.loads((root / 'source/git.json').read_text()), configured['source'])
        self.assertFalse(cache.exists(), 'saving a source must not fetch or create a receiver cache')

        before, state = target_files(home), self.cli(*arguments, 'status')
        preview = self.cli(*arguments, 'preview-sync')
        self.assertEqual(preview['outcome'], 'preview')
        self.assertEqual(preview['version'], 'v1')
        self.assertEqual(preview['source']['commit'], first)
        self.assertTrue(preview['changes'])
        self.assertIsNone(state['active'])
        self.assertEqual(self.cli(*arguments, 'status'), state)
        self.assertEqual(target_files(home), before)
        self.assertTrue(cache.is_dir())

        result = self.cli(*arguments, 'sync', '--at', '2026-10-20T10:00:00+09:00')
        self.assertEqual(result['outcome'], 'updated')
        state = self.cli(*arguments, 'status')
        self.assertEqual(state['active']['source']['commit'], first)

        second = remote.publish('v2')
        before = target_files(home)
        preview = self.cli(*arguments, 'preview-sync')
        self.assertEqual(preview['outcome'], 'preview')
        self.assertEqual(preview['version'], 'v2')
        self.assertEqual(preview['source']['commit'], second)
        self.assertTrue(preview['changes'])
        self.assertEqual(target_files(home), before)
        self.assertEqual(self.cli(*arguments, 'status'), state)
        result = self.cli(*arguments, 'sync', '--at', '2026-10-21T10:00:00+09:00')
        self.assertEqual(result['outcome'], 'updated')
        state = self.cli(*arguments, 'status')
        self.assertEqual(state['active']['source']['commit'], second)
        self.assertEqual(state['active']['bundle']['version'], 'v2')

    def test_invalid_endpoint_is_rejected_without_saving_or_fetching(self):
        root = Path(self.cli('init')['sandbox'])
        before = {path.relative_to(root).as_posix(): path.read_bytes()
                  for path in root.rglob('*') if path.is_file()}
        paths = set(root.rglob('*'))
        result = self.cli(
            '--sandbox', root, '--pc', 'personal', 'configure-git',
            '--url', 'ext::unsupported-offline-fixture', '--branch', 'main', exit_code=1,
        )
        self.assertEqual(result['outcome'], 'failed')
        self.assertFalse((root / 'source/git.json').exists())
        self.assertFalse((root / 'personal/.local/share/ai-knowledge/receive-cache.git').exists())
        self.assertEqual(set(root.rglob('*')), paths)
        self.assertEqual({path.relative_to(root).as_posix(): path.read_bytes()
                          for path in root.rglob('*') if path.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
