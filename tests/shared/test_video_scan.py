"""Tests for lib/video_scan.py — episode token detection and selection."""

from pathlib import Path

import pytest

from lib.video_scan import (
    episode_token, list_video_files, parse_episode_spec, select_videos,
)


@pytest.mark.parametrize('name, expected', [
    ('Mahou Tsukai Sally - 064 [DVD].mkv', 'EP064'),
    ('[YYDM-11FANS][UFO_Ultramaiden_Valkyrie][SP01][DVDRIP]'
     '[960x720][X264-10bit(vfr)_AAC][2xAudio-jp+en][A3D3AE54].mp4', 'SP01'),
    ('Show OVA02 1080p.mkv', 'OVA02'),
    ('Show EP7.mkv', 'EP007'),
    ('[Group] Show - 12 - Title [A3D3AE54].mkv', 'EP012'),
    ('random_name.mkv', 'random_name'),
])
def test_episode_token_variants(name, expected):
    assert episode_token(name) == expected


def test_episode_token_ignores_resolution_and_bit_depth():
    token = episode_token('[960x720][10bit][A3D3AE54].mkv')
    # no bare episode number → fallback to stem, never a bogus EP number
    assert token == '[960x720][10bit][A3D3AE54]'


@pytest.mark.parametrize('spec, expected', [
    ('1-3', ['EP001', 'EP002', 'EP003']),
    ('EP001,EP005', ['EP001', 'EP005']),
    ('3-1', ['EP001', 'EP002', 'EP003']),
    ('SP01-SP02', ['SP01', 'SP02']),
    ('1,1,2', ['EP001', 'EP002']),
    ('', []),
])
def test_parse_episode_spec(spec, expected):
    assert parse_episode_spec(spec) == expected


def _touch(path: Path):
    path.write_bytes(b'')
    return path


def test_list_video_files_sorted_and_filtered(tmp_path):
    _touch(tmp_path / 'ep10.mkv')
    _touch(tmp_path / 'ep2.mkv')
    _touch(tmp_path / 'notes.txt')
    files = list_video_files(str(tmp_path))
    assert [Path(f).name for f in files] == ['ep2.mkv', 'ep10.mkv']


def test_list_video_files_dedupes_same_directory(tmp_path):
    _touch(tmp_path / 'ep1.mkv')
    files = list_video_files([str(tmp_path), str(tmp_path)])
    assert len(files) == 1


def test_select_videos_filters_and_limits(tmp_path):
    for n in (1, 2, 3, 4):
        _touch(tmp_path / f'Show - {n:03d}.mkv')

    picked = select_videos(str(tmp_path), episodes='2-3')
    assert [t for t, _ in picked] == ['EP002', 'EP003']

    limited = select_videos(str(tmp_path), limit=2)
    assert [t for t, _ in limited] == ['EP001', 'EP002']

    started = select_videos(str(tmp_path), start_from='3')
    assert [t for t, _ in started] == ['EP003', 'EP004']
