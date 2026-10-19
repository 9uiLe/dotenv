"""Path guards for the prototype's generated, repository-local mock homes."""

import json
import os
from pathlib import Path
import tempfile


REPOSITORY = Path(__file__).absolute().parents[1]
SANDBOXES = REPOSITORY / '.prototype'
MARKER = '.ai-knowledge-sandbox.json'


def _without_symlinks(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('sandbox path contains a symbolic link')


def create_sandbox(prefix: str = 'validation-') -> Path:
    """Create an isolated pair of empty mock homes; never use the real HOME."""
    if not isinstance(prefix, str) or not prefix or '/' in prefix or '\\' in prefix:
        raise ValueError('invalid sandbox prefix')
    _without_symlinks(SANDBOXES)
    SANDBOXES.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=SANDBOXES))
    (root / MARKER).write_text(json.dumps({'schema': 1}) + '\n', encoding='utf-8')
    for profile in ('personal', 'work'):
        (root / profile).mkdir()
    return root


def check_home(home: Path) -> Path:
    """Accept only personal/work directly below a marked generated sandbox."""
    supplied = Path(home).expanduser()
    if '..' in supplied.parts:
        raise ValueError('sandbox path contains parent traversal')
    home = Path(os.path.abspath(supplied))
    root = home.parent
    if home.name not in ('personal', 'work') or root.parent != SANDBOXES:
        raise ValueError('home must be a generated personal/work sandbox')
    _without_symlinks(home)
    if not root.is_dir():
        raise ValueError('sandbox root is missing')
    marker = root / MARKER
    _without_symlinks(marker)
    if not marker.is_file():
        raise ValueError('sandbox marker is missing')
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
    # Walk without following links, including links outside the selected profile.
    def unreadable(error):
        raise ValueError('sandbox contains an unreadable directory') from error

    for directory, subdirectories, filenames in os.walk(
            root, followlinks=False, onerror=unreadable):
        for name in (*subdirectories, *filenames):
            if (Path(directory) / name).is_symlink():
                raise ValueError('sandbox contains a symbolic link')
    return home
