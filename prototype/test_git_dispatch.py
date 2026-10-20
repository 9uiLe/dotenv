"""隔離したPC設定と実Gitで、共通フックと既存フックの共存を確認する。"""

import hashlib
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from ai_knowledge.git_dispatch import DATA, HOOK_EVENTS


REPOSITORY = Path(__file__).resolve().parents[1]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


class GitDispatchTests(unittest.TestCase):
    def setUp(self):
        root = REPOSITORY / '.prototype'
        root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='git-dispatch-', dir=root)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write(self.root / '.ai-knowledge-sandbox.json', encoded({'schema': 1}))
        self.home = self.root / 'personal'
        self.home.mkdir(mode=0o700)
        self.data = self.home / DATA
        self.dispatcher = self.data / 'runtime/ai_knowledge/git_dispatch.py'
        source = (REPOSITORY / 'ai_knowledge/git_dispatch.py').read_text(encoding='utf-8')
        self.write(self.dispatcher, source)
        self.write(self.data / 'installation.json', encoded({
            'schema': 1, 'home': str(self.home), 'profile': 'personal', 'mode': 'mock',
            'python': str(Path(sys.executable).absolute()),
            'runtime_hashes': {'ai_knowledge/git_dispatch.py':
                               hashlib.sha256(source.encode('utf-8')).hexdigest()},
        }))
        checker = (REPOSITORY / 'shared/hooks/token_limit.py').read_text(encoding='utf-8')
        self.bundle = {'version': 'test', 'rules': '', 'hooks': [],
                       'files': {'hooks/token_limit.py': checker}}
        self.save_state()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update({
            'HOME': str(self.home), 'GIT_CONFIG_GLOBAL': str(self.home / '.gitconfig'),
            'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
        })
        self.repo = self.root / 'unrelated-project'
        self.repo.mkdir()
        self.git('init', '--quiet', '--template=')
        for key, value in {
            'user.name': 'Git Dispatch Test', 'user.email': 'test@example.invalid',
            'commit.gpgsign': 'false', 'tag.gpgsign': 'false', 'core.autocrlf': 'false',
        }.items():
            self.git('config', '--local', key, value)
        self.configure()

    def write(self, path, text, *, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        path.chmod(0o700 if executable else 0o600)
        return path

    def save_state(self, *, phase='ready', enabled=True, bundle=None):
        bundle = self.bundle if bundle is None else bundle
        revision = hashlib.sha256(encoded(bundle).encode('utf-8')).hexdigest()
        for name, text in bundle['files'].items():
            self.write(self.data / 'releases' / revision / name, text)
        self.state = {'schema': 1, 'phase': phase, 'hooks_enabled': enabled,
                      'active': {'bundle': bundle, 'revision': revision}}
        self.write(self.data / 'state.json', encoded(self.state))
        return revision

    def configure(self, previous=None):
        directory = self.data / 'git-hooks'
        for event in HOOK_EVENTS:
            command = shlex.join([
                sys.executable, str(self.dispatcher), '--home', str(self.home),
                '--previous-hooks-path-json', json.dumps(previous), event,
            ])
            self.write(directory / event, '#!/bin/sh\nexec ' + command + ' "$@"\n', executable=True)
        global_config = self.home / '.gitconfig'
        global_config.unlink(missing_ok=True)
        if previous is not None:
            self.git('config', '--global', 'core.hooksPath', previous)
        include = self.data / 'git-config'
        self.write(include, '[core]\n\thooksPath = ' + json.dumps(str(directory)) + '\n')
        self.git('config', '--global', '--add', 'include.path', str(include))

    def git(self, *arguments, check=True, cwd=None, env=None):
        result = subprocess.run(['git', *arguments], cwd=cwd or self.repo, env=env or self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def staged(self, name='authored.txt', text='small\n', *, cwd=None):
        cwd = cwd or self.repo
        self.write(cwd / name, text)
        self.git('add', '--', name, cwd=cwd)

    def commit(self, *, accepted=True, cwd=None, extra=()):
        result = self.git('commit', '--quiet', '-m', 'fixture', *extra, cwd=cwd, check=False)
        if accepted:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, '拒否すべきコミットが成功しました')
        return result

    def existing_hook(self, event, body, *, directory=None):
        return self.write((directory or self.repo / '.git/hooks') / event,
                          '#!/bin/sh\n' + body, executable=True)

    def invoke(self, event, *arguments, input=None, cwd=None):
        return subprocess.run([str(self.data / 'git-hooks' / event), *arguments],
                              cwd=cwd or self.repo, env=self.env, input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_global_hook_rejects_other_project_and_keeps_staged_scope(self):
        self.staged(text='x' * 20_001)
        self.write(self.repo / 'authored.txt', 'small working tree\n')
        result = self.commit(accepted=False)
        self.assertIn('20,001', result.stderr)
        self.assertIn('分割', result.stderr)
        self.assertIn('git add', result.stderr)
        self.assertEqual(self.git('config', '--local', '--get', 'core.hooksPath', check=False).returncode, 1)

    def test_native_precommit_and_commit_message_hook_both_run(self):
        marker = self.root / 'precommit-ran'
        self.existing_hook('pre-commit', 'printf ran > ' + shlex.quote(str(marker)) + '\n')
        self.existing_hook('commit-msg', 'printf "\\n既存フックで追記\\n" >> "$1"\n')
        self.staged()
        self.commit()
        self.assertEqual(marker.read_text(), 'ran')
        self.assertIn('既存フックで追記', self.git('log', '-1', '--format=%B').stdout)

    def test_existing_hook_failure_is_propagated(self):
        self.existing_hook('pre-commit', 'printf "既存フックが拒否\\n" >&2\nexit 7\n')
        self.staged()
        result = self.invoke('pre-commit')
        self.assertEqual(result.returncode, 7)
        self.assertIn('既存フックが拒否', result.stderr)
        self.commit(accepted=False)

    def test_failed_token_check_stops_before_existing_hook(self):
        marker = self.root / 'unexpected'
        self.existing_hook('pre-commit', 'touch ' + shlex.quote(str(marker)) + '\n')
        self.staged(text='x' * 20_001)
        self.commit(accepted=False)
        self.assertFalse(marker.exists())

    def test_previous_absolute_global_hooks_replace_native_hooks(self):
        previous = self.root / 'previous hooks'
        self.existing_hook('pre-commit', 'exit 8\n')
        self.existing_hook('pre-commit', 'printf old-global\\n\n', directory=previous)
        self.configure(str(previous))
        self.staged()
        result = self.commit()
        self.assertIn('old-global', result.stdout + result.stderr)

    def test_previous_relative_global_hooks_use_git_hook_cwd(self):
        self.existing_hook('pre-commit', 'test "$PWD" = ' + shlex.quote(str(self.repo)) + '\n',
                           directory=self.repo / 'relative hooks')
        self.configure('relative hooks')
        self.staged()
        self.commit()

    def test_previous_tilde_global_hooks_expand_home(self):
        self.existing_hook('pre-commit', 'exit 9\n', directory=self.home / 'custom-hooks')
        self.configure('~/custom-hooks')
        self.staged()
        self.assertEqual(self.invoke('pre-commit').returncode, 9)

    def test_conditional_global_include_is_evaluated_in_current_project(self):
        previous = self.home / 'conditional-hooks'
        self.existing_hook('pre-commit', 'printf conditional-hook >&2\nexit 12\n', directory=previous)
        conditional = self.home / 'conditional.gitconfig'
        self.write(conditional, '[core]\n\thooksPath = ' + json.dumps(str(previous)) + '\n')
        managed = self.data / 'git-config'
        self.write(self.home / '.gitconfig',
                   '[includeIf "gitdir:' + str(self.repo / '.git') + '"]\n\tpath = '
                   + json.dumps(str(conditional)) + '\n[include]\n\tpath = '
                   + json.dumps(str(managed)) + '\n')
        self.staged()
        result = self.commit(accepted=False)
        self.assertIn('conditional-hook', result.stderr)

    def test_checker_only_does_not_reenter_previous_hooks(self):
        self.existing_hook('pre-commit', 'exit 13\n')
        self.staged()
        command = [sys.executable, str(self.dispatcher), '--home', str(self.home),
                   '--previous-hooks-path-json', 'null', '--check-only', 'pre-commit']
        result = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.staged(text='x' * 20_001)
        result = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('分割', result.stderr)

    def test_remote_url_conditional_include_can_see_local_remote(self):
        previous = self.home / 'remote-hooks'
        self.existing_hook('pre-commit', 'printf remote-hook >&2\nexit 14\n', directory=previous)
        conditional = self.home / 'remote.gitconfig'
        self.write(conditional, '[core]\n\thooksPath = ' + json.dumps(str(previous)) + '\n')
        managed = self.data / 'git-config'
        self.write(self.home / '.gitconfig',
                   '[includeIf "hasconfig:remote.*.url:https://example.invalid/**"]\n\tpath = '
                   + json.dumps(str(conditional)) + '\n[include]\n\tpath = '
                   + json.dumps(str(managed)) + '\n')
        self.git('remote', 'add', 'origin', 'https://example.invalid/project.git')
        self.staged()
        result = self.commit(accepted=False)
        self.assertIn('remote-hook', result.stderr)

    def test_existing_system_hook_path_is_preserved(self):
        previous = self.root / 'system-hooks'
        self.existing_hook('pre-commit', 'printf system-hook >&2\nexit 15\n', directory=previous)
        configuration = self.root / 'system.gitconfig'
        self.write(configuration, '[core]\n\thooksPath = ' + json.dumps(str(previous)) + '\n')
        self.env['GIT_CONFIG_NOSYSTEM'] = '0'
        self.env['GIT_CONFIG_SYSTEM'] = str(configuration)
        self.staged()
        result = self.commit(accepted=False)
        self.assertIn('system-hook', result.stderr)

    def test_existing_custom_xdg_configuration_is_preserved(self):
        previous = self.root / 'xdg-hooks'
        self.existing_hook('pre-commit', 'printf xdg-hook >&2\nexit 16\n', directory=previous)
        xdg = self.root / 'custom-config'
        self.write(xdg / 'git/config', '[core]\n\thooksPath = ' + json.dumps(str(previous)) + '\n')
        self.env.pop('GIT_CONFIG_GLOBAL')
        self.env['XDG_CONFIG_HOME'] = str(xdg)
        self.staged()
        result = self.commit(accepted=False)
        self.assertIn('xdg-hook', result.stderr)

    def test_existing_hook_without_shebang_uses_git_shell_fallback(self):
        self.write(self.repo / '.git/hooks/pre-commit', 'printf shell-hook >&2\nexit 17\n', executable=True)
        self.staged()
        result = self.invoke('pre-commit')
        self.assertEqual(result.returncode, 17)
        self.assertIn('shell-hook', result.stderr)

    def test_stdin_arguments_output_and_exit_are_preserved_for_other_events(self):
        self.existing_hook('pre-push', 'printf "%s\\n" "$1" "$2"\ncat\nprintf error >&2\nexit 11\n')
        result = self.invoke('pre-push', 'origin', 'remote path', input='stdin payload\n')
        self.assertEqual(result.returncode, 11)
        self.assertEqual(result.stdout, 'origin\nremote path\nstdin payload\n')
        self.assertEqual(result.stderr, 'error')

    def test_linked_worktree_uses_shared_native_hook_and_its_own_index(self):
        self.staged()
        self.commit()
        worktree = self.root / 'linked'
        self.git('worktree', 'add', '--quiet', '-b', 'linked', str(worktree))
        marker = self.root / 'worktree-hook'
        self.existing_hook('pre-commit', 'printf "%s" "$PWD" > ' + shlex.quote(str(marker)) + '\n')
        self.staged('worktree.txt', cwd=worktree)
        self.commit(cwd=worktree)
        self.assertEqual(marker.read_text(), str(worktree))
        self.staged('worktree.txt', 'x' * 20_001, cwd=worktree)
        self.commit(accepted=False, cwd=worktree)

    def test_commit_only_keeps_git_temporary_index(self):
        self.staged('selected.txt')
        self.staged('other.txt')
        self.commit()
        self.staged('other.txt', 'x' * 20_001)
        self.write(self.repo / 'selected.txt', 'selected change\n')
        self.commit(extra=('--only', 'selected.txt'))
        self.commit(accepted=False)

    def test_bare_repository_default_hooks_work(self):
        bare = self.root / 'bare.git'
        self.git('init', '--quiet', '--bare', '--template=', str(bare))
        self.existing_hook('post-update', 'printf "%s" "$1"\n', directory=bare / 'hooks')
        result = self.invoke('post-update', 'refs/heads/main', cwd=bare)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'refs/heads/main')

    def test_pause_and_inactive_phases_keep_existing_hooks(self):
        self.existing_hook('pre-commit', 'printf existing\\n\n')
        for phase, enabled in [('ready', False),
                               ('recovery_required', True), ('uninstalled', True)]:
            with self.subTest(phase=phase, enabled=enabled):
                self.save_state(phase=phase, enabled=enabled)
                self.staged(text='x' * 20_001)
                result = self.invoke('pre-commit')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('existing', result.stdout)

    def test_update_in_progress_rejects_commit_with_retry_instructions(self):
        self.staged()
        self.save_state(phase='applying')
        self.assertIn('再実行', self.commit(accepted=False).stderr)
        self.save_state()
        with (self.data / 'operation.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn('更新中', self.commit(accepted=False).stderr)

    def test_release_tampering_and_revision_mismatch_fail_closed(self):
        self.staged()
        revision = self.state['active']['revision']
        self.write(self.data / 'releases' / revision / 'hooks/token_limit.py', 'raise SystemExit(0)\n')
        self.assertIn('配布内容が一致', self.commit(accepted=False).stderr)
        self.save_state()
        self.state['active']['revision'] = '0' * 64
        self.write(self.data / 'state.json', encoded(self.state))
        self.assertIn('版と共有設定が一致', self.commit(accepted=False).stderr)

    def test_runtime_tampering_fails_closed(self):
        self.staged()
        self.write(self.dispatcher, self.dispatcher.read_text() + '\n# changed\n')
        self.assertIn('ランタイムが変更', self.commit(accepted=False).stderr)

    def test_missing_state_rejects_commit_with_reinstallation_instructions(self):
        self.staged(text='x' * 20_001)
        (self.data / 'state.json').unlink()
        result = self.commit(accepted=False)
        self.assertIn('管理状態がありません', result.stderr)
        self.assertIn('再導入', result.stderr)

    def test_unrelated_home_symlink_does_not_trigger_full_tree_walk(self):
        (self.home / 'unrelated-link').symlink_to(self.repo, target_is_directory=True)
        self.staged()
        self.commit()

    def test_previous_path_to_dispatcher_cannot_recurse(self):
        self.configure(str(self.data / 'git-hooks'))
        self.staged()
        self.commit()

    def test_local_hook_path_override_remains_effective(self):
        local = self.repo / 'project-hooks'
        self.existing_hook('pre-commit', 'printf project-hook\\n\n', directory=local)
        self.git('config', '--local', 'core.hooksPath', str(local))
        self.staged(text='x' * 20_001)
        result = self.commit()
        self.assertIn('project-hook', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
