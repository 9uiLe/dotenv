"""一時 Git リポジトリの実コミットで staged ファイルの制限を確認する。"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HOOKS_DIRECTORY = Path(__file__).resolve().parents[1] / '.githooks'


class TokenHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='token-hook-')
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repo'
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_')}
        self.env.update({
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
        })
        self.git('init', '--quiet', '--template=')
        for key, value in {
            'user.name': 'Token Hook Test',
            'user.email': 'token-hook@example.invalid',
            'commit.gpgsign': 'false',
            'tag.gpgsign': 'false',
            'core.autocrlf': 'false',
            'core.hooksPath': str(HOOKS_DIRECTORY),
        }.items():
            self.git('config', '--local', key, value)
        hook = HOOKS_DIRECTORY / 'pre-commit'
        self.assertTrue(hook.is_file(), f'フックがありません: {hook}')
        self.assertTrue(os.access(hook, os.X_OK), f'フックに実行権限がありません: {hook}')

    def git(self, *arguments, check=True):
        result = subprocess.run(
            ['git', *arguments], cwd=self.repo, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    def stage(self, *names):
        self.git('add', '--', *names)

    def commit(self, *, accepted=True, bypass=False):
        arguments = ['commit', '--quiet', '-m', 'fixture']
        if bypass:
            arguments.append('--no-verify')
        result = self.git(*arguments, check=False)
        if accepted:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, '上限を超えたコミットが成功しました')
        return result

    def test_boundary_and_actionable_japanese_error(self):
        self.write('limit.txt', b'x' * 20_000)
        self.stage('limit.txt')
        self.commit()

        self.write('limit.txt', b'x' * 20_001)
        self.stage('limit.txt')
        result = self.commit(accepted=False)
        self.assertIn('limit.txt', result.stderr)
        self.assertIn('5000', result.stderr.replace(',', '').replace('_', ''))
        self.assertRegex(result.stderr, r'(?:bytes|バイト数)\s*/\s*4')
        for instruction in ('責務', '分割', 'import', 'include', 'リンク', 'git add', 'コミット'):
            self.assertIn(instruction, result.stderr)
        self.assertEqual(self.git('show', 'HEAD:limit.txt').stdout, 'x' * 20_000)

    def test_measures_utf8_bytes_instead_of_characters(self):
        self.write('日本語.txt', 'あ' * 6_667)
        self.stage('日本語.txt')
        self.commit(accepted=False)

    def test_uses_small_staged_blob_despite_large_unstaged_content(self):
        self.write('partial.txt', b'small staged content\n')
        self.stage('partial.txt')
        self.write('partial.txt', b'x' * 20_001)
        self.commit()
        self.assertEqual(self.git('show', 'HEAD:partial.txt').stdout, 'small staged content\n')

    def test_rejects_large_staged_blob_despite_small_unstaged_content(self):
        self.write('partial.txt', b'x' * 20_001)
        self.stage('partial.txt')
        self.write('partial.txt', 'small worktree content\n')
        self.commit(accepted=False)

    def test_ignores_untracked_and_unchanged_large_files(self):
        self.write('legacy.txt', b'x' * 20_001)
        self.stage('legacy.txt')
        self.commit(bypass=True)
        self.write('untracked.txt', b'x' * 20_001)
        self.write('small.txt', 'small\n')
        self.stage('small.txt')
        self.commit()

    def test_only_deleting_a_large_file_can_commit(self):
        self.write('legacy.txt', b'x' * 20_001)
        self.stage('legacy.txt')
        self.commit(bypass=True)
        self.git('rm', '--', 'legacy.txt')
        self.commit()

    def test_checks_renamed_destination(self):
        self.write('generated/legacy.txt', b'x' * 20_001)
        self.stage('generated/legacy.txt')
        self.commit()
        self.git('mv', '--', 'generated/legacy.txt', 'authored.txt')
        result = self.commit(accepted=False)
        self.assertIn('authored.txt', result.stderr)

    def test_ignores_known_lockfiles_and_generated_or_dependency_paths(self):
        names = (
            'package-lock.json', 'nested/yarn.lock', 'pnpm-lock.yaml',
            'Cargo.lock', 'poetry.lock', 'Gemfile.lock', 'go.sum',
            'generated/api.py', 'nested/__generated__/types.ts',
            'vendor/library.py', 'nested/node_modules/library.js',
            'api.generated.ts', 'model.g.cs', 'service_pb2.py',
            'service_pb2_grpc.py', 'service.pb.go',
        )
        for name in names:
            self.write(name, b'x' * 20_001)
        self.stage(*names)
        self.commit()

    def test_does_not_exempt_authored_paths_with_similar_names(self):
        for name in ('generated-notes.md', 'vendor-guide.md', 'lockfile_notes.md'):
            self.write(name, b'x' * 20_001)
        self.stage('.')
        result = self.commit(accepted=False)
        for name in ('generated-notes.md', 'vendor-guide.md', 'lockfile_notes.md'):
            self.assertIn(name, result.stderr)

    def test_staged_attributes_exempt_generated_vendored_and_custom_files(self):
        attributes = (
            'custom.txt token-limit-ignore\n'
            'generated.txt linguist-generated=true\n'
            'vendored.txt linguist-vendored\n'
        )
        self.write('.gitattributes', attributes)
        for name in ('custom.txt', 'generated.txt', 'vendored.txt'):
            self.write(name, b'x' * 20_001)
        self.stage('.')
        self.write('.gitattributes', '')
        self.commit()

    def test_unstaged_attributes_cannot_hide_a_staged_large_file(self):
        self.write('authored.txt', b'x' * 20_001)
        self.stage('authored.txt')
        self.write('.gitattributes', 'authored.txt token-limit-ignore\n')
        self.commit(accepted=False)

    def test_false_unset_and_unspecified_attributes_do_not_exempt_files(self):
        self.write('.gitattributes', (
            'false.txt token-limit-ignore=false\n'
            'unset.txt -linguist-generated\n'
            'other.txt linguist-vendored=other\n'
        ))
        for name in ('false.txt', 'unset.txt', 'other.txt'):
            self.write(name, b'x' * 20_001)
        self.stage('.')
        result = self.commit(accepted=False)
        for name in ('false.txt', 'unset.txt', 'other.txt'):
            self.assertIn(name, result.stderr)

    def test_ignores_symlinks_and_gitlinks(self):
        self.write('target.txt', b'x' * 20_001)
        (self.repo / 'link.txt').symlink_to('target.txt')
        self.stage('link.txt')
        self.commit()
        head = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{head},submodule')
        self.commit()
        self.assertEqual(self.git('ls-files', '-s', '--', 'link.txt').stdout[:6], '120000')
        self.assertEqual(self.git('ls-files', '-s', '--', 'submodule').stdout[:6], '160000')

    def test_handles_spaces_tabs_newlines_and_leading_dashes_in_paths(self):
        names = ('-leading.txt', '空 白\t改行\n.txt')
        for name in names:
            self.write(name, b'x' * 20_001)
        self.stage(*names)
        result = self.commit(accepted=False)
        self.assertIn('-leading.txt', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        for name in names:
            self.write(name, 'small\n')
        self.stage(*names)
        self.commit()


if __name__ == '__main__':
    unittest.main()
