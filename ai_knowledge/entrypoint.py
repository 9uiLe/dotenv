"""Stable entry point copied into the user's private runtime directory."""

from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11以上が必要です')

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_knowledge.cli import main

if __name__ == '__main__':
    raise SystemExit(main(installation=Path(__file__).resolve().parents[2] / 'installation.json'))
