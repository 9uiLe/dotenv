"""実際に配る原本の形式・Python構文・見本Hookの整合性を保証する。"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from ai_knowledge.shared_source import load_local


SHARED = Path(__file__).resolve().parents[1] / 'shared'


class SharedSourceTests(unittest.TestCase):
    def test_committed_source_is_a_valid_distribution(self):
        bundle = load_local(SHARED)
        self.assertTrue(bundle['rules'].strip())
        self.assertTrue(bundle['skills'])
        self.assertEqual(set(bundle['instructions']), {'codex', 'claude'})
        self.assertTrue(all(text.strip() for text in bundle['instructions'].values()))

    def test_every_distributed_python_file_has_valid_syntax(self):
        for name, text in load_local(SHARED)['files'].items():
            if name.endswith('.py'):
                with self.subTest(name=name):
                    compile(text, name, 'exec')

    def test_session_hook_announces_the_committed_shared_version(self):
        bundle = load_local(SHARED)
        result = subprocess.run(
            [sys.executable, '-B', str(SHARED / 'hooks/session_start.py')],
            input='{}', capture_output=True, text=True, check=True, timeout=10,
        )
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'SessionStart')
        self.assertEqual(output['additionalContext'], '共有設定 ' + bundle['version'])


if __name__ == '__main__':
    unittest.main()
