"""Explicit boundaries for an installed Mac and isolated installation rehearsals."""

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import stat
import sys

from ai_knowledge.engine import RULES, SCHEDULE, SKILLS
from ai_knowledge.sandbox import check_home


def _normal(path):
    supplied = Path(path).expanduser()
    if '..' in supplied.parts:
        raise ValueError('管理対象に親フォルダへの移動は指定できません')
    return Path(os.path.abspath(supplied))


def _check_path(path, *, directory=False):
    """Inspect only this path's existing components; never follow a link."""
    for candidate in reversed((path, *path.parents)):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError('管理対象のパスにシンボリックリンクがあります')
        leaf = candidate == path and not directory
        if not stat.S_ISDIR(metadata.st_mode) and not (leaf and stat.S_ISREG(metadata.st_mode)):
            raise ValueError('管理対象に通常ファイル・フォルダ以外があります')


@dataclass(frozen=True)
class Target:
    home: Path
    profile: str
    mode: str

    def __post_init__(self):
        object.__setattr__(self, 'home', _normal(self.home))
        if self.profile not in {'personal', 'work'} or self.mode not in {'live', 'mock'}:
            raise ValueError('導入先の種類またはプロファイルが不正です')
        self.guard()

    @classmethod
    def live(cls, profile, home=None):
        return cls(Path(pwd.getpwuid(os.getuid()).pw_dir) if home is None else home,
                   profile, 'live')

    @classmethod
    def mock(cls, home, profile=None):
        home = _normal(home)
        return cls(home, profile or home.name, 'mock')

    @property
    def data(self):
        return self.home / '.local/share/ai-knowledge'

    @property
    def source(self):
        return self.data / 'source'

    @property
    def runtime(self):
        return self.data / 'runtime'

    @property
    def python(self):
        return Path(sys.executable).absolute()

    @property
    def tools(self):
        return ('codex',) if self.profile == 'personal' else ('codex', 'claude')

    def _allowed(self, name):
        if not isinstance(name, str):
            return False
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute()
                or '..' in relative.parts or str(relative) != name or '\\' in name):
            return False
        if name == SCHEDULE or name.startswith('.local/share/ai-knowledge/'):
            return True
        if name in {RULES[tool] for tool in self.tools}:
            return True
        if name in {'.codex/config.toml', '.codex/hooks.json', '.codex/AGENTS.override.md', '.gitconfig'}:
            return True
        if self.profile == 'work' and name == '.claude/settings.json':
            return True
        for tool in self.tools:
            prefix = SKILLS[tool]
            if name.startswith(prefix):
                parts = PurePosixPath(name[len(prefix):]).parts
                return len(parts) >= 2 and parts[0].startswith('ai-knowledge-')
        return False

    def path(self, relative):
        if not self._allowed(relative):
            raise ValueError('管理対象外のパスにはアクセスできません')
        path = self.home / relative
        _check_path(path)
        return path

    def guard(self, home=None):
        if home is not None and _normal(home) != self.home:
            raise ValueError('導入先のhomeが一致しません')
        if self.mode == 'mock':
            if self.home.name != self.profile:
                raise ValueError('模擬PCとプロファイルが一致しません')
            return check_home(self.home)
        if (platform.system() != 'Darwin' or os.getuid() == 0
                or os.geteuid() != os.getuid()):
            raise ValueError('本番導入は通常ユーザー権限のMacで実行してください')
        standard = _normal(pwd.getpwuid(os.getuid()).pw_dir)
        if self.home != standard:
            raise ValueError('本番導入は実行ユーザーの標準homeだけに限定しています')
        _check_path(self.home, directory=True)
        relatives = [RULES[tool] for tool in self.tools]
        relatives += ['.codex/config.toml', '.codex/hooks.json', '.codex/AGENTS.override.md', '.gitconfig', SCHEDULE]
        if self.profile == 'work':
            relatives.append('.claude/settings.json')
        for relative in relatives:
            self.path(relative)
        _check_path(self.data, directory=True)
        # The application's private data is managed; unrelated home trees are not.
        if self.data.exists():
            metadata = self.data.lstat()
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError('管理フォルダは本人所有・本人だけアクセス可能にしてください（700）')
            def unreadable(error):
                raise ValueError('管理フォルダを確認できません') from error

            for directory, folders, files in os.walk(self.data, followlinks=False, onerror=unreadable):
                for name in folders:
                    entry = Path(directory) / name
                    _check_path(entry, directory=True)
                    if entry.lstat().st_uid != os.getuid():
                        raise ValueError('管理フォルダ内に本人所有でないフォルダがあります')
                for name in files:
                    entry = Path(directory) / name
                    _check_path(entry)
                    metadata = entry.lstat()
                    if metadata.st_uid != os.getuid():
                        raise ValueError('管理フォルダ内に本人所有でないファイルがあります')
                    relative = entry.relative_to(self.data)
                    private = (str(relative) in {'installation.json', 'state.json', 'pending.json',
                                                'source/git.json'}
                               or (relative.parts[0] == 'backups' and name == 'before.json'))
                    if private and stat.S_IMODE(metadata.st_mode) & 0o077:
                        raise ValueError('管理JSONは本人だけが読み書き可能にしてください（600）')
        return self.home
