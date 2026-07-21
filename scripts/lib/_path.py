"""Bootstrap module — import before any lib.* imports to ensure scripts/ is on sys.path.

Usage (one line, replaces 4-line boilerplate):
    import lib._path  # noqa: F401

All 13 executable scripts under scripts/ use this single source of truth.
lib/ modules themselves do NOT import _path — their callers already have.
"""
import os
import sys


# Absolute path to the scripts/ directory — exposed for scripts that need to
# construct paths to sibling packages (e.g. os.path.join(SCRIPTS_DIR, 'nouns', ...)).
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup():
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)


_setup()
