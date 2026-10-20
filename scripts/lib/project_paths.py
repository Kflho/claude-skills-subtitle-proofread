#!/usr/bin/env python3
"""Project output layout: ``data/`` for intermediates, ``bin/`` for outputs.

The house convention (see the 阿童木 project heading 目录约定) is:

    <project>/data/temp/     one-off intermediates (scans, ASR, anchors)
    <project>/data/reports/  generated reports
    <project>/bin/exports/   deliverables, one dated dir per release
    <project>/bin/reference_docs/  inputs (reference subtitles, source subs)

Older projects keep ``temp/`` and ``reports/`` at the project root.  So the
rule is: **if ``<project>/data`` exists, write under it; otherwise fall back to
the legacy root dirs**.  Migration is therefore just ``mkdir data`` + moving
``temp/`` and ``reports/`` inside, and no project breaks mid-way.

All helpers take an optional ``project_dir`` (default: cwd) and never create
directories; callers create what they need.
"""

from __future__ import annotations

import os
from datetime import date


def project_root(project_dir: str | None = None) -> str:
    return os.path.abspath(project_dir or os.getcwd())


def data_root(project_dir: str | None = None) -> str:
    """``<project>/data`` when it exists, else the project root (legacy)."""
    root = project_root(project_dir)
    data = os.path.join(root, 'data')
    return data if os.path.isdir(data) else root


def temp_root(project_dir: str | None = None) -> str:
    """Directory for one-off intermediates (``data/temp`` or legacy ``temp``)."""
    return os.path.join(data_root(project_dir), 'temp')


def scans_dir(project_dir: str | None = None) -> str:
    return os.path.join(temp_root(project_dir), 'scans')


def reports_root(project_dir: str | None = None) -> str:
    """Directory for generated reports (``data/reports`` or legacy ``reports``)."""
    return os.path.join(data_root(project_dir), 'reports')


def report_path(project_dir: str | None = None, name: str = '问题解决报告.md') -> str:
    return os.path.join(reports_root(project_dir), name)


def review_dir(project_dir: str | None = None) -> str:
    return os.path.join(reports_root(project_dir), 'manual-review')


def exports_root(project_dir: str | None = None) -> str:
    """``bin/exports`` — deliverables live in dated subdirectories."""
    return os.path.join(project_root(project_dir), 'bin', 'exports')


def export_dir(project_dir: str | None = None, stamp: str | None = None) -> str:
    """Dated release directory, e.g. ``bin/exports/261021``."""
    stamp = stamp or date.today().strftime('%y%m%d')
    return os.path.join(exports_root(project_dir), stamp)


def reference_docs(project_dir: str | None = None) -> str:
    """``bin/reference_docs`` — inputs such as reference subtitles."""
    return os.path.join(project_root(project_dir), 'bin', 'reference_docs')
