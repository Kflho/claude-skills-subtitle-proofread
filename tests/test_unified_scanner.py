"""Tests for scan/unified_scanner.py — repeat/gap/parse/scan helpers."""

from scan.unified_scanner import (
    _compute_gap_statistics, _find_repeats, _is_excluded_repeat,
    _parse_episodes, _should_scan, get_oped_boundaries, scan_file,
)

from lib.config import ED_BOUNDARY_SEC, OP_BOUNDARY_SEC


def cue(start, end, text='x'):
    return {'start': f'00:00:{start:02d},000', 'end': f'00:00:{end:02d},000',
            'start_s': float(start), 'end_s': float(end), 'text': text}


# ═══════════════════════════════════════════════════════════════
# Episode parsing / filtering
# ═══════════════════════════════════════════════════════════════

def test_parse_episodes_ranges_and_lists():
    assert _parse_episodes('EP001-EP003') == ['EP001', 'EP002', 'EP003']
    assert _parse_episodes('1,3') == ['EP001', 'EP003']
    assert _parse_episodes('5') == ['EP005']
    assert _parse_episodes(None) is None


def test_should_scan():
    assert _should_scan('EP001', None) is True
    assert _should_scan('EP001', ['EP001']) is True
    assert _should_scan('EP002', ['EP001']) is False


# ═══════════════════════════════════════════════════════════════
# Gap statistics / OP-ED boundaries
# ═══════════════════════════════════════════════════════════════

def test_gap_statistics_counts_suspicious_gaps():
    stats = _compute_gap_statistics([
        cue(0, 5), cue(6, 10), cue(40, 45),
    ])
    assert stats['total_gaps'] == 2
    assert stats['suspicious_gap_count'] == 1
    assert stats['max_gap_s'] == 30.0

    assert _compute_gap_statistics([cue(0, 5)])['total_gaps'] == 0


def test_oped_boundaries_follow_config():
    cues = [cue(0, 10), cue(700, 710)]
    op, ed = get_oped_boundaries(cues)
    assert op == OP_BOUNDARY_SEC
    assert ed == 710 - ED_BOUNDARY_SEC


# ═══════════════════════════════════════════════════════════════
# Repeat detection
# ═══════════════════════════════════════════════════════════════

def test_find_repeats_detects_loop():
    text = 'あいう' * 10
    found = _find_repeats(text, min_repeats=8)
    assert found
    assert found[0]['repeat_seq'] == 'あいう'
    assert found[0]['repeat_count'] == 10


def test_find_repeats_ignores_short_text():
    assert _find_repeats('あいう', min_repeats=8) == []


def test_excluded_repeats():
    assert _is_excluded_repeat('pa', 'papapapa') is True
    assert _is_excluded_repeat('あい', 'あいあい') is True     # only 2 chars
    assert _is_excluded_repeat('あいう', 'あいう' * 8) is False


# ═══════════════════════════════════════════════════════════════
# scan_file
# ═══════════════════════════════════════════════════════════════

SRT = (
    "1\n00:00:10,000 --> 00:00:12,000\nこんにちは\n\n"
    "2\n00:06:40,000 --> 00:06:42,000\nhello world\n\n"
    "3\n00:11:40,000 --> 00:11:42,000\nこんばんは\n\n"
)


def test_scan_file_flags_garbled_outside_oped(tmp_path):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    result = scan_file(str(path), skip_oped=True, target_lang='ja')

    assert result['filename'] == 'EP001.srt'
    assert result['total_cues'] == 3
    assert len(result['issues']) == 1
    assert result['issues'][0]['start'].startswith('00:06:40')
    assert len(result['garbled_cues']) == 1
    assert 'gap_statistics' in result


def test_scan_file_empty_file(tmp_path):
    path = tmp_path / 'empty.srt'
    path.write_text('', encoding='utf-8')
    result = scan_file(str(path))
    assert result['total_cues'] == 0
    assert result['issues'] == []
