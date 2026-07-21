"""Bootstrap module — import before any lib.* imports to ensure scripts/ is on sys.path.

Usage (one line, replaces 4-line boilerplate):
    import lib._path  # noqa: F401

All 13 executable scripts under scripts/ use this single source of truth.
lib/ modules themselves do NOT import _path — their callers already have.
"""
import os
import sys


def _setup():
    _lib_dir = os.path.dirname(os.path.abspath(__file__))
    _root_dir = os.path.dirname(_lib_dir)  # lib/ → scripts/
    if _root_dir not in sys.path:
        sys.path.insert(0, _root_dir)


_setup()
