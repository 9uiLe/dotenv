"""一時領域に生成した模擬ホームの境界と安全性を検証する。"""

import json
import os
from pathlib import Path
import stat
import tempfile


SANDBOXES = Path(tempfile.gettempdir()).resolve() / f'ai-knowledge-{os.getuid()}'
MARKER = '.ai-knowledge-sandbox.json'


def _without_symlinks(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('sandbox path contains a symbolic link')


def _private_directory(path: Path) -> None:
    _without_symlinks(path)
    metadata = path.stat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise ValueError('sandbox directory must be owned by the current user with mode 700')


def create_sandbox(prefix: str = 'validation-') -> Path:
    """Create an isolated pair of empty mock homes; never use the real HOME."""
    if not isinstance(prefix, str) or not prefix or '/' in prefix or '\\' in prefix:
        raise ValueError('invalid sandbox prefix')
    _without_symlinks(SANDBOXES)
    SANDBOXES.mkdir(mode=0o700, exist_ok=True)
    _private_directory(SANDBOXES)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=SANDBOXES))
    marker = root / MARKER
    marker.write_text(json.dumps({'schema': 1}) + '\n', encoding='utf-8')
    marker.chmod(0o600)
    for profile in ('personal', 'work'):
        (root / profile).mkdir()
    return root


def check_home(home: Path, *, scan_tree: bool = True) -> Path:
    """Accept only personal/work directly below a marked generated sandbox."""
    supplied = Path(home).expanduser()
    if '..' in supplied.parts:
        raise ValueError('sandbox path contains parent traversal')
    home = Path(os.path.abspath(supplied))
    root = home.parent
    if home.name not in ('personal', 'work') or root.parent != SANDBOXES:
        raise ValueError('home must be a generated personal/work sandbox')
    _without_symlinks(home)
    _private_directory(SANDBOXES)
    _private_directory(root)
    marker = root / MARKER
    _without_symlinks(marker)
    metadata = marker.stat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600):
        raise ValueError('sandbox marker must be a private regular file')
    try:
        if marker.stat().st_size > 4096:
            raise ValueError('sandbox marker is invalid')
        document = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('sandbox marker is invalid') from error
    if (not isinstance(document, dict) or set(document) != {'schema'}
            or type(document['schema']) is not int or document['schema'] != 1):
        raise ValueError('sandbox marker is invalid')
    if home.exists() and not home.is_dir():
        raise ValueError('sandbox home is not a directory')
    if not scan_tree:
        return home
    # Walk without following links, including links outside the selected profile.
    def unreadable(error):
        raise ValueError('sandbox contains an unreadable directory') from error

    for directory, subdirectories, filenames in os.walk(
            root, followlinks=False, onerror=unreadable):
        for name in (*subdirectories, *filenames):
            metadata = (Path(directory) / name).lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError('sandbox contains a symbolic link')
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                raise ValueError('sandbox contains a special file')
    return home
