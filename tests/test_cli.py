"""公開CLIの導入文法と入力拒否を、隔離した一時ホームで確認する。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from ai_knowledge.sandbox import create_sandbox
from tests.git_fixtures import LocalRemote


REPOSITORY = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix='cli-test-')
        self.addCleanup(shutil.rmtree, self.root)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith('GIT_')
                            and key not in {'PYTHONPATH', 'PYTHONHOME'}}
        self.environment.update({'GIT_CONFIG_NOSYSTEM': '1',
                                 'GIT_CONFIG_SYSTEM': os.devnull,
                                 'GIT_CONFIG_GLOBAL': os.devnull})

    def invoke(self, *arguments, exit_code=0, parse=True):
        result = subprocess.run(
            [sys.executable, '-B', '-m', 'ai_knowledge', *map(str, arguments)],
            capture_output=True, text=True, cwd=REPOSITORY,
            env=self.environment, timeout=30,
        )
        self.assertEqual(result.returncode, exit_code, result.stderr + result.stdout)
        if not parse:
            return result
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def cli(self, *arguments, **kwargs):
        return self.invoke('--sandbox', self.root, '--profile', 'work', *arguments, **kwargs)

    def test_plan_apply_and_status_work_across_separate_processes(self):
        remote = LocalRemote(self.root)
        commit = remote.publish('v1')
        home = self.root / 'work'
        before = snapshot(home)
        self.assertEqual(self.cli('status'), {'outcome': 'not_installed'})
        preview = self.cli('plan', '--url', remote.remote, '--branch', 'main')
        self.assertEqual(preview['outcome'], 'preview')
        self.addCleanup(shutil.rmtree, Path(preview['plan']).parent)
        self.assertEqual(snapshot(home), before)
        applied = self.cli('apply', '--plan', preview['plan'], '--apply')
        self.assertEqual(applied['outcome'], 'installed', applied)
        status = self.cli('status')
        self.assertEqual(status['outcome'], 'status')
        self.assertEqual(status['version'], 'v1')
        self.assertEqual(status['source']['commit'], commit)
        self.assertTrue(status['scheduler']['registered'])
        self.assertTrue((home / '.claude/CLAUDE.md').is_file())
        self.assertEqual(snapshot(self.root / 'personal'), {})

    def test_invalid_source_and_plan_do_not_change_target_home(self):
        before = snapshot(self.root / 'work')
        result = self.cli('plan', '--url', 'ext::unsupported-offline-fixture', exit_code=1)
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(snapshot(self.root / 'work'), before)
        malformed = self.root / 'invalid-plan.json'
        malformed.write_text('[]\n', encoding='utf-8')
        malformed.chmod(0o600)
        result = self.cli('apply', '--plan', malformed, '--apply', exit_code=1)
        self.assertEqual(result['outcome'], 'failed')
        self.assertEqual(snapshot(self.root / 'work'), before)
        usage = self.cli('apply', '--plan', malformed, exit_code=2, parse=False)
        self.assertIn('--apply', usage.stderr)
        self.assertEqual(snapshot(self.root / 'work'), before)

    def test_unmarked_and_outside_sandboxes_are_rejected_without_writes(self):
        unmarked = self.root / 'unmarked'
        unmarked.mkdir()
        outside_directory = tempfile.TemporaryDirectory(prefix='ai-knowledge-outside-')
        self.addCleanup(outside_directory.cleanup)
        outside = Path(outside_directory.name)
        (outside / '.ai-knowledge-sandbox.json').write_text('{"schema": 1}\n')
        for root in (unmarked, outside):
            with self.subTest(root=root):
                (root / 'work').mkdir()
                (root / 'keep-local.txt').write_text('keep unchanged\n')
                before, paths = snapshot(root), set(root.rglob('*'))
                result = self.invoke('--sandbox', root, 'status', exit_code=1)
                self.assertEqual(result['outcome'], 'failed')
                self.assertEqual(snapshot(root), before)
                self.assertEqual(set(root.rglob('*')), paths)


if __name__ == '__main__':
    unittest.main()
