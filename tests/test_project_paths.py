"""Tests for lib.project_paths — data/ layout with legacy fallback."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import project_paths as P  # noqa: E402


def test_legacy_layout_when_data_missing(tmp_path):
    assert P.data_root(str(tmp_path)) == str(tmp_path)
    assert P.temp_root(str(tmp_path)) == str(tmp_path / 'temp')
    assert P.reports_root(str(tmp_path)) == str(tmp_path / 'reports')
    assert P.scans_dir(str(tmp_path)) == str(tmp_path / 'temp' / 'scans')


def test_data_layout_when_data_exists(tmp_path):
    (tmp_path / 'data').mkdir()
    assert P.data_root(str(tmp_path)) == str(tmp_path / 'data')
    assert P.temp_root(str(tmp_path)) == str(tmp_path / 'data' / 'temp')
    assert P.reports_root(str(tmp_path)) == str(tmp_path / 'data' / 'reports')
    assert P.scans_dir(str(tmp_path)) == str(tmp_path / 'data' / 'temp' / 'scans')


def test_report_and_review_paths_follow_reports_root(tmp_path):
    (tmp_path / 'data').mkdir()
    assert P.report_path(str(tmp_path)).endswith(('问题解决报告.md', '問題解決報告.md'))
    assert 'data' in Path(P.report_path(str(tmp_path))).parts
    assert P.review_dir(str(tmp_path)) == str(tmp_path / 'data' / 'reports' / 'manual-review')


def test_export_dir_is_dated_under_bin_exports(tmp_path):
    out = P.export_dir(str(tmp_path), '261021')
    assert out == str(tmp_path / 'bin' / 'exports' / '261021')
    assert P.exports_root(str(tmp_path)) == str(tmp_path / 'bin' / 'exports')


def test_export_dir_defaults_to_today(tmp_path):
    from datetime import date
    out = P.export_dir(str(tmp_path))
    assert Path(out).name == date.today().strftime('%y%m%d')


def test_reference_docs_points_at_bin(tmp_path):
    assert P.reference_docs(str(tmp_path)) == str(tmp_path / 'bin' / 'reference_docs')


def test_helpers_do_not_create_directories(tmp_path):
    P.temp_root(str(tmp_path))
    P.reports_root(str(tmp_path))
    P.export_dir(str(tmp_path), '261021')
    assert not (tmp_path / 'temp').exists()
    assert not (tmp_path / 'reports').exists()
    assert not (tmp_path / 'bin').exists()
