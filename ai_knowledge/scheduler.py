"""User LaunchAgent registration, with an explicit adapter for offline validation."""

import os
from pathlib import Path
import pwd
import subprocess
import sys


LABEL = 'local.ai-knowledge.sync'
PLIST = f'Library/LaunchAgents/{LABEL}.plist'
LAUNCHCTL = '/bin/launchctl'
NOT_FOUND = 113


class SchedulerError(RuntimeError):
    pass


class Scheduler:
    """Register only the current non-root Mac user's dedicated LaunchAgent.

    A supplied runner is an explicit OS adapter. Tests use FakeLaunchctl and
    never invoke launchctl. State flags and the operation lock belong to the
    caller: persist the paused flag before unregistering, and pause again when
    registration fails.
    """

    def __init__(self, home, *, runner=None, uid=None):
        supplied = Path(home)
        if not supplied.is_absolute() or '..' in supplied.parts:
            raise ValueError('ホームは親参照のない絶対パスで指定してください')
        self.home = supplied
        self.uid = os.getuid() if uid is None else uid
        if type(self.uid) is not int or self.uid < 1:
            raise ValueError('日次更新は一般ユーザーで登録してください')
        self.domain = f'gui/{self.uid}'
        self.service = f'{self.domain}/{LABEL}'
        self.plist = self.home / PLIST
        self._adapter = runner
        if runner is None:
            self._check_live()

    def _check_live(self):
        current = os.getuid()
        if (sys.platform != 'darwin' or current < 1 or os.geteuid() != current
                or self.uid != current
                or self.home != Path(pwd.getpwuid(current).pw_dir)):
            raise ValueError('実際のOS登録はMacのログインユーザー本人に限定しています')
        if not self.home.is_dir() or any(path.is_symlink() for path in
                                        (self.home, *self.home.parents)):
            raise ValueError('実際のホームにシンボリックリンクは使用できません')

    def _call(self, action, *targets):
        if self._adapter is None:
            self._check_live()
        runner = self._adapter or subprocess.run
        try:
            return runner(
                [LAUNCHCTL, action, *targets],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=15, check=False, shell=False, close_fds=True,
                cwd=str(self.home),
                env={'HOME': str(self.home), 'PATH': '/usr/bin:/bin',
                     'LANG': 'C', 'LC_ALL': 'C'},
            )
        except subprocess.TimeoutExpired as error:
            raise SchedulerError('OSの日次更新操作が時間内に完了しませんでした') from error
        except OSError as error:
            raise SchedulerError('OSの日次更新操作を実行できませんでした') from error

    def _registered(self):
        result = self._call('print', self.service)
        if result.returncode == 0:
            return True
        if result.returncode == NOT_FOUND:
            return False
        raise SchedulerError('OSの日次更新の登録状態を確認できませんでした')

    def status(self):
        try:
            return {'outcome': 'status', 'registered': self._registered()}
        except (SchedulerError, ValueError) as error:
            return {'outcome': 'failed', 'reason': str(error)}

    def register(self):
        try:
            if self._registered():
                return {'outcome': 'registered', 'registered': True, 'changed': False}
            if (not self.plist.is_file() or self.plist.is_symlink()
                    or any(path.is_symlink() for path in self.plist.parents)):
                raise SchedulerError('登録する日次更新の設定ファイルがありません')
            # bootstrap accepts the domain followed by the plist path.
            result = self._call('bootstrap', self.domain, str(self.plist))
            if result.returncode != 0 or not self._registered():
                raise SchedulerError('OSへの日次更新の登録に失敗しました。自動更新を停止してください')
            return {'outcome': 'registered', 'registered': True, 'changed': True}
        except (SchedulerError, ValueError) as error:
            return {'outcome': 'failed', 'reason': str(error)}

    def unregister(self):
        try:
            if not self._registered():
                return {'outcome': 'unregistered', 'registered': False, 'changed': False}
            result = self._call('bootout', self.service)
            # A competing stop may already have removed the service. Confirm
            # the final state even when bootout reports a missing service.
            if result.returncode not in (0, NOT_FOUND) or self._registered():
                raise SchedulerError('OSの日次更新の登録解除に失敗しました。停止状態を確認してください')
            return {'outcome': 'unregistered', 'registered': False, 'changed': True}
        except (SchedulerError, ValueError) as error:
            return {'outcome': 'failed', 'reason': str(error)}


class FakeLaunchctl:
    """In-memory launchctl adapter; captures requests without starting a process."""

    def __init__(self, registered=False, *, failures=None):
        self.registered = registered
        self.calls = []
        self.options = []
        self.failures = dict(failures or {})

    def __call__(self, arguments, **options):
        self.calls.append(list(arguments))
        self.options.append(dict(options))
        if len(arguments) < 3 or arguments[0] != LAUNCHCTL:
            raise ValueError('fake adapter only accepts launchctl requests')
        action = arguments[1]
        failure = self.failures.get(action)
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return subprocess.CompletedProcess(arguments, failure, '', '')
        if action == 'print' and len(arguments) == 3:
            code = 0 if self.registered else NOT_FOUND
        elif action == 'bootstrap' and len(arguments) == 4:
            code = 5 if self.registered else 0
            self.registered = True
        elif action == 'bootout' and len(arguments) == 3:
            code = 0 if self.registered else NOT_FOUND
            self.registered = False
        else:
            raise ValueError('unsupported fake launchctl request')
        return subprocess.CompletedProcess(arguments, code, '', '')
