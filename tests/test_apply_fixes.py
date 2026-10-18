"""Tests for apply/apply_fixes.py — cue-level and file-level fixes."""

from pathlib import Path

import pytest

from apply.apply_fixes import (
    _norm_timecode, _resolve_ass_fix_lines, apply_delete_comment,
    apply_delete_line, apply_delete_style, apply_merge_cues,
    apply_replace_global, apply_replace_global_regex, apply_replace_text,
)


def cue(start, end, text):
    return {'start': start, 'end': end,
            'start_s': float(start[-6:-4]), 'end_s': float(end[-6:-4]),
            'text': text}


def test_replace_text_found_missing_and_idempotent():
    cues = [cue('00:00:01,000', '00:00:02,000', 'old')]
    ok, msg = apply_replace_text(cues, {'start': '00:00:01,000',
                                        'replacement': 'new'})
    assert ok and cues[0]['text'] == 'new'

    ok, msg = apply_replace_text(cues, {'start': '00:00:09,000',
                                        'replacement': 'x'})
    assert not ok and '未找到' in msg

    ok, msg = apply_replace_text(cues, {'start': '00:00:01,000',
                                        'replacement': 'new'})
    assert ok and 'already correct' in msg


def test_delete_line_removes_cue():
    cues = [cue('00:00:01,000', '00:00:02,000', 'a'),
            cue('00:00:03,000', '00:00:04,000', 'b')]
    ok, _ = apply_delete_line(cues, {'start': '00:00:01,000'})
    assert ok
    assert [c['text'] for c in cues] == ['b']

    ok, msg = apply_delete_line(cues, {'start': '00:00:01,000'})
    assert ok and 'already deleted' in msg


def test_merge_cues_joins_text_and_end():
    cues = [cue('00:00:01,000', '00:00:02,000', 'a'),
            cue('00:00:03,000', '00:00:04,000', 'b'),
            cue('00:00:05,000', '00:00:06,000', 'c')]
    ok, _ = apply_merge_cues(cues, {'start': '00:00:01,000', 'count': 2})
    assert ok
    assert len(cues) == 2
    assert cues[0]['text'] == 'a\nb'
    assert cues[0]['end'] == '00:00:04,000'


def test_merge_cues_rejects_insufficient_count():
    cues = [cue('00:00:01,000', '00:00:02,000', 'a')]
    ok, msg = apply_merge_cues(cues, {'start': '00:00:01,000', 'count': 3})
    assert not ok and '不足' in msg


def test_norm_timecode_treats_srt_and_ass_equally():
    assert _norm_timecode('00:00:04,000') == _norm_timecode('0:00:04.00')
    assert _norm_timecode('') == ''
    assert _norm_timecode('00:00:04.12') == '00:00:04.12'


def test_resolve_ass_fix_lines_by_line_and_start():
    lines = [
        '[Events]\n',
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n',
        'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,b\n',
    ]
    fixes = [{'start': '0:00:05.00', 'replacement': 'x'},
             {'line': 2, 'replacement': 'y'}]
    resolved = _resolve_ass_fix_lines(lines, fixes)
    assert [idx for idx, _, _ in resolved] == [2, 1]
    assert resolved[0][2] == 'start'
    assert resolved[1][2] == 'line'


def test_delete_style_and_comment():
    lines = [
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n',
        'Dialogue: 0,0:00:02.00,0:00:03.00,Sign,,0,0,0,,b\n',
        'Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Translated by\n',
    ]
    ok, msg = apply_delete_style(lines, {'style': 'Sign'})
    assert ok and '1' in msg
    assert lines[1] == ''
    assert 'Sign' not in ''.join(lines)

    ok, msg = apply_delete_comment(lines, {'keyword': 'Translated by'})
    assert ok and lines[2] == ''


SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nFoo bar\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nFoo baz\n\n"
)


def test_replace_global_dry_run_does_not_touch_file(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')
    ok, msg = apply_replace_global(str(path),
                                   {'original': 'Foo', 'replacement': 'Bar'},
                                   dry_run=True)
    assert ok and '2' in msg
    assert 'Foo bar' in path.read_text(encoding='utf-8')
    assert not (tmp_path / 'ep.srt.bak').exists()


def test_replace_global_writes_backup_and_new_text(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')
    ok, msg = apply_replace_global(str(path),
                                   {'original': 'Foo', 'replacement': 'Bar'})
    assert ok
    text = path.read_text(encoding='utf-8')
    assert 'Bar bar' in text and 'Bar baz' in text
    bak = tmp_path / 'ep.srt.bak'
    assert bak.exists() and 'Foo bar' in bak.read_text(encoding='utf-8')


def test_replace_global_regex_dry_run(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')
    ok, msg = apply_replace_global_regex(
        str(path), {'pattern': r'F\w+', 'replacement': 'X'}, dry_run=True)
    assert ok and '2' in msg
    assert 'Foo' in path.read_text(encoding='utf-8')
