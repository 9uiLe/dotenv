"""A local-only Git stand-in for GitHub; never pushes or contacts a service."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from prototype.fixtures import make_bundle
from prototype.sandbox import check_home


class LocalRemote:
    """Create a fresh mock remote. Call configure() explicitly after publish()."""

    def __init__(self, root: Path):
        self.root = check_home(Path(root) / 'personal').parent
        self.remote = self.root / 'remote.git'
        self.remote.mkdir()
        self._git('init', '--initial-branch=main', '.')

    def _git(self, *arguments):
        # Ignore inherited Git configuration, identity, tracing, and helper settings.
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        environment.update({
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_AUTHOR_NAME': 'Offline Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
            'GIT_COMMITTER_NAME': 'Offline Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
        })
        completed = subprocess.run(
            ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
             '-c', 'tag.gpgsign=false', '-c', 'core.fsmonitor=false',
             '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
             '-C', str(self.remote), *arguments],
            env=environment, capture_output=True, text=True, check=True, timeout=15,
        )
        return completed.stdout.strip()

    def _write(self, relative, content):
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or '\\' in relative:
            raise ValueError('fixture source path must stay inside shared')
        destination = self.remote / 'shared' / relative
        if any(candidate.is_symlink() for candidate in (destination, *destination.parents)):
            raise ValueError('fixture source path contains a symbolic link')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding='utf-8')

    def publish(self, version='v1', bundle=None):
        """Write and locally commit one source tree; older files remain on disk."""
        bundle = make_bundle(version) if bundle is None else bundle
        self._write('rules.md', bundle['rules'])
        for relative, content in bundle['files'].items():
            self._write(relative, content)
        manifest = {
            'schema': 1, 'version': bundle['version'], 'rules': 'rules.md',
            'files': list(bundle['files']), 'hooks': bundle['hooks'],
        }
        self._write('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        self._git('add', '--', 'shared')
        self._git('commit', '--allow-empty', '-m', 'Offline sample ' + bundle['version'])
        return self._git('rev-parse', 'HEAD')

    def reset(self, commit):
        """Move the local main ref backwards without deleting the worktree files."""
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40,64}', commit):
            raise ValueError('reset requires a full local commit SHA')
        self._git('rev-parse', '--verify', commit + '^{commit}')
        self._git('update-ref', 'refs/heads/main', commit)

    def configure(self):
        """Write only this sandbox's local receiver configuration."""
        check_home(self.root / 'personal')
        source = self.root / 'source'
        source.mkdir(exist_ok=True)
        config = {'url': str(self.remote), 'branch': 'main', 'manifest': 'shared/manifest.json'}
        (source / 'git.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        return config
