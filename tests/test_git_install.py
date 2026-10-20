"""模擬PCへ配布した共通Gitフックを、別プロジェクトの実コミットで確認する。"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import shutil
import unittest

from ai_knowledge.target import Target
from tests.fixtures import make_bundle
from tests.git_fixtures import LocalRemote
from ai_knowledge.sandbox import create_sandbox


CHECKER = Path(__file__).resolve().parents[1] / 'shared/hooks/token_limit.py'


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def snapshot(home):
    return {str(path.relative_to(home)): path.read_bytes()
            for path in home.rglob('*') if path.is_file()}


class GitInstallationTests(unittest.TestCase):
    def setUp(self):
        self.root = create_sandbox(prefix='git-install-test-')
        self.addCleanup(shutil.rmtree, self.root)
        self.remote = LocalRemote(self.root)
        self.publish('v1')
        self.target = Target.mock(self.root / 'personal')
        self.config = {'url': str(self.remote.remote), 'branch': 'main',
                       'manifest': 'shared/manifest.json'}
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key not in {'PYTHONPATH', 'PYTHONHOME'}}
        self.env.update({
            'HOME': str(self.target.home),
            'XDG_CONFIG_HOME': str(self.target.home / '.config'),
            'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0',
        })

    def publish(self, version, *, checker=True):
        bundle = make_bundle(version)
        if checker:
            bundle['files']['hooks/token_limit.py'] = CHECKER.read_text(encoding='utf-8')
        return self.remote.publish(version, bundle=bundle)

    def create(self):
        from ai_knowledge.installer import create_plan
        plan = create_plan(self.target, self.config)
        self.addCleanup(shutil.rmtree, Path(plan['plan']).parent)
        self.assertEqual(plan['outcome'], 'preview', plan)
        return plan

    def install(self):
        from ai_knowledge.installer import apply_plan
        result = apply_plan(self.create()['plan'])
        self.assertEqual(result['outcome'], 'installed', result)
        return result

    def operate(self, command, **kwargs):
        from ai_knowledge.cli import operate
        return operate(self.target, command, **kwargs)

    def state(self):
        from ai_knowledge.runtime import LiveManager
        return LiveManager(self.target).status()

    def git(self, repo, *arguments, check=True):
        result = subprocess.run(
            ['git', *arguments], cwd=repo, env=self.env,
            capture_output=True, text=True, timeout=30,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def repository(self, name='unrelated-project'):
        repo = self.target.home / name
        repo.mkdir()
        self.git(repo, 'init', '--quiet', '--template=')
        for key, value in {'user.name': 'Offline Fixture',
                           'user.email': 'fixture@example.invalid',
                           'commit.gpgsign': 'false', 'tag.gpgsign': 'false',
                           'core.autocrlf': 'false'}.items():
            self.git(repo, 'config', '--local', key, value)
        return repo

    def stage(self, repo, name, content):
        write_text(repo / name, content)
        self.git(repo, 'add', '--', name)

    def commit(self, repo, *, accepted=True):
        result = self.git(repo, 'commit', '--quiet', '-m', 'offline fixture', check=False)
        if accepted:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, '上限超過のコミットが成功しました')
        return result

    def script(self, directory, event, text):
        path = directory / event
        write_text(path, '#!/bin/sh\n' + text)
        path.chmod(0o755)
        return path

    def log_hook(self, directory, event, log, *, exit_code=0):
        return self.script(directory, event,
                           f'printf "%s\\n" {shlex.quote(event)} >> {shlex.quote(str(log))}\n'
                           f'exit {exit_code}\n')

    def previous_global(self):
        directory = self.target.home / 'existing hooks'
        original = ('# existing user settings\n[user]\n\tname = Existing User\n'
                    '[core]\n\thooksPath = ' + json.dumps(str(directory)) + '\n')
        write_text(self.target.home / '.gitconfig', original)
        return directory, original

    def test_preview_is_read_only_and_an_unrelated_project_uses_the_installed_checker(self):
        from ai_knowledge.git_dispatch import HOOK_EVENTS
        from ai_knowledge.installer import apply_plan
        baseline = '# user configuration\n[alias]\n\tst = status\n'
        write_text(self.target.home / '.gitconfig', baseline)
        before = snapshot(self.target.home)
        plan = self.create()
        self.assertEqual(snapshot(self.target.home), before)
        self.assertEqual(apply_plan(plan['plan'])['outcome'], 'installed')
        data = self.target.data
        config = (self.target.home / '.gitconfig').read_text()
        self.assertTrue(config.startswith(baseline))
        self.assertIn('# ai-knowledge:git-hooks:begin', config)
        self.assertIn(str(data / 'git-config'), config)
        for event in HOOK_EVENTS:
            self.assertEqual((data / 'git-hooks' / event).stat().st_mode & 0o777, 0o755)

        repo = self.repository()
        self.assertEqual(self.git(repo, 'config', '--get', 'core.hooksPath').stdout.strip(),
                         str(data / 'git-hooks'))
        self.assertEqual(self.git(repo, 'config', '--local', '--get', 'core.hooksPath',
                                  check=False).returncode, 1)
        self.stage(repo, 'limit.txt', 'x' * 20_000)
        self.commit(repo)
        self.stage(repo, 'limit.txt', 'x' * 20_001)
        result = self.commit(repo, accepted=False)
        self.assertIn('limit.txt', result.stderr)
        for instruction in ('責務', '分割', 'import', 'git add'):
            self.assertIn(instruction, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

        # 作業ツリーと未追跡ファイルではなく、ステージ済み内容だけを検査する。
        self.stage(repo, 'limit.txt', 'small staged content\n')
        write_text(repo / 'limit.txt', 'x' * 20_001)
        write_text(repo / 'untracked.txt', 'x' * 20_001)
        self.stage(repo, 'package-lock.json', 'x' * 20_001)
        self.commit(repo)
        self.assertEqual(self.git(repo, 'show', 'HEAD:limit.txt').stdout, 'small staged content\n')

        # 別プロジェクトをcwdにして、コピー済みランタイムだけでも状態を読める。
        status = subprocess.run(
            [sys.executable, '-I', '-B', str(self.target.runtime / 'ai_knowledge/entrypoint.py'), 'status'],
            cwd=repo, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        self.assertEqual(json.loads(status.stdout)['version'], 'v1')

    def test_default_repository_hooks_and_other_events_keep_their_arguments(self):
        self.install()
        repo = self.repository()
        hooks, log = repo / '.git/hooks', self.target.home / 'hook.log'
        self.log_hook(hooks, 'pre-commit', log)
        self.log_hook(hooks, 'post-commit', log)
        self.script(hooks, 'commit-msg',
                    f'printf "commit-msg:%s\\n" "$1" >> {shlex.quote(str(log))}\n')
        self.stage(repo, 'small.txt', 'small\n')
        self.commit(repo)
        self.assertEqual(log.read_text().splitlines(),
                         ['pre-commit', 'commit-msg:.git/COMMIT_EDITMSG', 'post-commit'])
        # 共通設定を外すと、元からなかった.gitconfigも残さない。
        self.assertEqual(self.operate('uninstall', apply=True)['outcome'], 'uninstalled')
        self.assertFalse((self.target.home / '.gitconfig').exists())
        self.stage(repo, 'small.txt', 'changed\n')
        self.commit(repo)
        self.assertEqual(log.read_text().count('pre-commit\n'), 2)

    def test_existing_global_hooks_remain_effective_when_shared_hooks_pause_or_are_removed(self):
        directory, original = self.previous_global()
        log = self.target.home / 'global-hook.log'
        hook = self.log_hook(directory, 'pre-commit', log)
        hook_bytes = hook.read_bytes()
        self.install()
        repo = self.repository()
        self.assertEqual(self.operate('pause-sync')['outcome'], 'changed')
        self.stage(repo, 'large.txt', 'x' * 20_001)
        self.commit(repo, accepted=False)
        self.assertFalse(log.exists())
        self.assertEqual(self.operate('pause-hooks')['outcome'], 'changed')
        self.commit(repo)
        self.assertEqual(log.read_text(), 'pre-commit\n')
        self.assertEqual(self.operate('resume-hooks')['outcome'], 'changed')
        self.stage(repo, 'large.txt', 'x' * 20_002)
        self.commit(repo, accepted=False)
        self.assertEqual(log.read_text(), 'pre-commit\n')

        # 導入後にユーザーが増やした設定も、共有部分の取り外しで失わない。
        addition = '\n[alias]\n\tco = checkout\n'
        path = self.target.home / '.gitconfig'
        write_text(path, path.read_text() + addition)
        before = snapshot(self.target.home)
        self.assertEqual(self.operate('uninstall')['outcome'], 'preview')
        self.assertEqual(snapshot(self.target.home), before)
        self.assertEqual(self.operate('uninstall', apply=True)['outcome'], 'uninstalled')
        self.assertEqual(path.read_text(), original + addition)
        self.assertEqual(hook.read_bytes(), hook_bytes)
        self.assertEqual(self.git(repo, 'config', '--get', 'core.hooksPath').stdout.strip(), str(directory))
        self.commit(repo)
        self.assertEqual(log.read_text(), 'pre-commit\npre-commit\n')

    def test_failure_in_an_existing_global_pre_commit_still_blocks_a_commit(self):
        directory, _ = self.previous_global()
        log = self.target.home / 'rejected.log'
        self.log_hook(directory, 'pre-commit', log, exit_code=17)
        self.install()
        repo = self.repository()
        self.stage(repo, 'small.txt', 'small\n')
        self.commit(repo, accepted=False)
        self.assertEqual(log.read_text(), 'pre-commit\n')
        self.assertNotEqual(self.git(repo, 'rev-parse', '--verify', 'HEAD', check=False).returncode, 0)

    def test_repository_local_hooks_path_has_priority_without_being_rewritten(self):
        repo = self.repository()
        hooks, log = repo / 'custom-hooks', self.target.home / 'local-hook.log'
        self.log_hook(hooks, 'pre-commit', log)
        self.git(repo, 'config', '--local', 'core.hooksPath', str(hooks))
        before = (repo / '.git/config').read_bytes()
        self.install()
        self.assertEqual((repo / '.git/config').read_bytes(), before)
        self.assertEqual(self.git(repo, 'config', '--get', 'core.hooksPath').stdout.strip(), str(hooks))
        self.stage(repo, 'large.txt', 'x' * 20_001)
        self.commit(repo)
        self.assertEqual(log.read_text(), 'pre-commit\n')

    def test_a_local_hook_can_call_the_shared_checker_without_running_itself_again(self):
        self.install()
        repo = self.repository()
        helper = self.target.data / 'git-hooks/check-token-limit'
        self.assertTrue(os.access(helper, os.X_OK), str(helper))
        hooks, log = repo / 'custom-hooks', self.target.home / 'local-checker.log'
        self.script(hooks, 'pre-commit',
                    f'{shlex.quote(str(helper))} || exit "$?"\n'
                    f'printf "local pre-commit\\n" >> {shlex.quote(str(log))}\n')
        self.git(repo, 'config', '--local', 'core.hooksPath', str(hooks))
        self.stage(repo, 'large.txt', 'x' * 20_001)
        result = self.commit(repo, accepted=False)
        self.assertIn('large.txt', result.stderr)
        self.assertIn('分割', result.stderr)
        self.assertFalse(log.exists())
        self.stage(repo, 'large.txt', 'small\n')
        self.commit(repo)
        self.assertEqual(log.read_text(), 'local pre-commit\n')

    def test_restoring_a_bundle_without_the_checker_restores_the_previous_global_configuration(self):
        # 旧共有版にはGitフックがなく、後日の同期で追加された構成を再現する。
        self.publish('before-git-hooks', checker=False)
        directory, original = self.previous_global()
        log = self.target.home / 'restored-hook.log'
        self.log_hook(directory, 'pre-commit', log)
        self.install()
        self.assertEqual((self.target.home / '.gitconfig').read_text(), original)
        self.publish('with-git-hooks')
        tomorrow = datetime.fromisoformat(self.state()['last_success_at']) + timedelta(days=1)
        result = self.operate('sync', at=tomorrow.replace(hour=12))
        self.assertEqual(result['outcome'], 'updated', result)
        repo = self.repository()
        self.stage(repo, 'large.txt', 'x' * 20_001)
        self.commit(repo, accepted=False)
        before = snapshot(self.target.home)
        self.assertEqual(self.operate('restore')['outcome'], 'preview')
        self.assertEqual(snapshot(self.target.home), before)
        result = self.operate('restore', apply=True)
        self.assertEqual(result['outcome'], 'restored', result)
        self.assertEqual(self.state()['active']['bundle']['version'], 'before-git-hooks')
        self.assertEqual((self.target.home / '.gitconfig').read_text(), original)
        self.assertFalse((self.target.data / 'git-config').exists())
        self.assertFalse((self.target.data / 'git-hooks/pre-commit').exists())
        self.commit(repo)
        self.assertEqual(log.read_text(), 'pre-commit\n')

    def test_gitconfig_changes_after_preview_block_installation(self):
        from ai_knowledge.installer import apply_plan
        _, original = self.previous_global()
        plan = self.create()
        write_text(self.target.home / '.gitconfig', original + '\n[alias]\n\tst = status\n')
        before = snapshot(self.target.home)
        with self.assertRaisesRegex(ValueError, '差分を再作成'):
            apply_plan(plan['plan'])
        self.assertEqual(snapshot(self.target.home), before)

    def test_included_global_hooks_path_changes_after_preview_block_installation(self):
        from ai_knowledge.installer import apply_plan
        included = self.target.home / 'existing-git.conf'
        write_text(included, '[core]\n\thooksPath = old-hooks\n')
        write_text(self.target.home / '.gitconfig',
                   '[include]\n\tpath = ' + json.dumps(str(included)) + '\n')
        plan = self.create()
        write_text(included, '[core]\n\thooksPath = changed-hooks\n')
        before = snapshot(self.target.home)
        with self.assertRaisesRegex(ValueError, '差分を再作成'):
            apply_plan(plan['plan'])
        self.assertEqual(snapshot(self.target.home), before)


if __name__ == '__main__':
    unittest.main()
