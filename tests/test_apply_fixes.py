"""Tests for apply/apply_fixes.py — cue-level and file-level fixes."""

import json
import sys
from pathlib import Path

import pytest

import utils.update_report as update_report
from apply import apply_fixes as af
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


# ═══════════════════════════════════════════════════════════════
# Helpers for main() / pipeline-level tests
# ═══════════════════════════════════════════════════════════════

def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def _invoke(monkeypatch, *args):
    """Call main() with a fake argv; returns its return value."""
    monkeypatch.setattr(sys, 'argv', ['apply_fixes.py', *[str(a) for a in args]])
    return af.main()


SRT_THREE = (
    "1\n00:00:01,000 --> 00:00:02,000\nFoo bar\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nFoo baz\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nBaz qux\n\n"
)

ASS_TEXT = (
    "[Script Info]\n"
    "Title: Test\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,旧甲\n"
    "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,旧乙\n"
    "Dialogue: 0,0:00:05.00,0:00:06.00,Sign,,0,0,0,,标识\n"
    "Comment: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,Translated by\n"
)

ASS_TRAD = (
    "[Script Info]\n"
    "Title: 繁體\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,繁體字幕\n"
    "Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,繁體註\n"
)


# ═══════════════════════════════════════════════════════════════
# Cue/file-level helpers — edge cases
# ═══════════════════════════════════════════════════════════════

def test_norm_timecode_handles_malformed_and_missing_values():
    assert _norm_timecode(None) == ''
    assert _norm_timecode('abc') == 'abc'       # no ':' at all
    assert _norm_timecode('00:04') == '00:04'   # not H:MM:SS
    assert _norm_timecode(' 1:2:3.456 ') == '01:02:03.45'


def test_resolve_ass_fix_lines_falls_back_to_start_and_marks_missing():
    lines = [
        '[Events]\n',
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n',
        'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,b\n',
        'Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,c\n',
    ]
    fixes = [
        {'line': 99, 'start': '0:00:05.00'},  # line out of range → start
        {'line': 0, 'start': '0:00:01.00'},   # line 0 is falsy → start
        {'start': '0:00:09.00'},              # no matching timecode
        {},                                    # neither line nor start
    ]
    resolved = _resolve_ass_fix_lines(lines, fixes)
    assert [(idx, matched) for idx, _, matched in resolved] == [
        (3, 'start'),   # duplicate timecode: last rendering wins
        (1, 'start'),
        (-1, ''),
        (-1, ''),
    ]


def test_apply_replace_global_and_regex_no_match_are_noops(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')

    ok, msg = apply_replace_global(
        str(path), {'original': 'Nope', 'replacement': 'X'})
    assert ok and 'already correct' in msg
    ok, msg = apply_replace_global_regex(
        str(path), {'pattern': 'Nope', 'replacement': 'X'})
    assert ok and 'already correct' in msg

    assert path.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'ep.srt.bak').exists()


def test_apply_replace_global_regex_writes_backup_and_new_text(tmp_path):
    path = tmp_path / 'ep.srt'
    path.write_text(SRT, encoding='utf-8')
    ok, msg = apply_replace_global_regex(
        str(path), {'pattern': r'F\w+', 'replacement': 'X'})
    assert ok and '2' in msg
    assert 'X bar' in path.read_text(encoding='utf-8')
    assert 'Foo bar' in (tmp_path / 'ep.srt.bak').read_text(encoding='utf-8')


def test_merge_cues_missing_start_and_single_line_mode():
    cues = [cue('00:00:01,000', '00:00:02,000', 'a'),
            cue('00:00:03,000', '00:00:04,000', 'b')]
    ok, msg = apply_merge_cues(cues, {'start': '00:00:09,000', 'count': 2})
    assert not ok and '未找到' in msg
    assert len(cues) == 2

    ok, _ = apply_merge_cues(cues, {'start': '00:00:01,000', 'count': 2,
                                    'multiline': False})
    assert ok and cues[0]['text'] == 'a b'
    assert cues[0]['end'] == '00:00:04,000'


def test_merge_cues_skips_blank_text_and_uses_last_end():
    cues = [cue('00:00:01,000', '00:00:02,000', 'a'),
            cue('00:00:03,000', '00:00:04,000', '   '),
            cue('00:00:05,000', '00:00:06,000', 'c')]
    ok, _ = apply_merge_cues(cues, {'start': '00:00:01,000', 'count': 3})
    assert ok
    assert len(cues) == 1
    assert cues[0]['text'] == 'a\nc'
    assert cues[0]['end'] == '00:00:06,000'


def test_delete_style_and_comment_no_match_return_false():
    lines = [
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,a\n',
        'Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,other\n',
        'Comment: too,few,fields\n',
    ]
    ok, msg = apply_delete_style(lines, {'style': 'Sign'})
    assert not ok and '0' in msg
    ok, msg = apply_delete_comment(lines, {'keyword': 'Translated by'})
    assert not ok and '0' in msg
    assert all(lines)  # nothing was blanked


# ═══════════════════════════════════════════════════════════════
# main() CLI — argument handling / early exits
# ═══════════════════════════════════════════════════════════════

def test_main_requires_fixes_or_mode(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _invoke(monkeypatch, '--target-dir', tmp_path)
    assert exc.value.code == 1
    assert '需要 --fixes 或 --review 或 --trad-to-simp' in capsys.readouterr().err


def test_main_review_option_is_deprecated(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _invoke(monkeypatch, '--target-dir', tmp_path,
                '--review', tmp_path / 'checklist.md')
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert '--review is deprecated' in err
    assert 'Aegisub' in err


def test_main_missing_fixes_json_raises(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        _invoke(monkeypatch, '--target-dir', tmp_path,
                '--fixes', tmp_path / 'missing.json')


def test_main_missing_target_dir_raises(tmp_path, monkeypatch):
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_global',
                         'original': 'a', 'replacement': 'b'}])
    with pytest.raises(FileNotFoundError):
        _invoke(monkeypatch, '--target-dir', tmp_path / 'nope',
                '--fixes', fixes)


def test_main_empty_fixes_list_is_noop(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '0 applied, 0 already correct, 0 skipped' in out
    assert path.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_main_skips_fix_without_file_or_global_scope(tmp_path, monkeypatch,
                                                     capsys):
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'delete_line'}])
    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '跳过无效修复项' in out
    assert '0 applied, 0 already correct, 0 skipped' in out


# ═══════════════════════════════════════════════════════════════
# main() — SRT per-file fixes
# ═══════════════════════════════════════════════════════════════

def test_main_srt_fixes_write_and_backup(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT_THREE, encoding='utf-8')
    other = tmp_path / 'unrelated.srt'
    other.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.srt',
         'start': '00:00:01.000', 'replacement': 'New bar'},
        {'action': 'delete_line', 'file': 'EP001.srt', 'line': 10},
        # Idempotent fix: text already matches.
        {'action': 'replace_text', 'file': 'EP001.srt',
         'start': '00:00:03.000', 'replacement': 'Foo baz'},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert 'EP001.srt: 2 applied' in out
    assert '2 applied, 1 already correct, 0 skipped' in out

    assert path.read_text(encoding='utf-8') == (
        "1\n00:00:01,000 --> 00:00:02,000\nNew bar\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nFoo baz\n\n"
    )
    assert (tmp_path / 'EP001.srt.bak').read_text(
        encoding='utf-8') == SRT_THREE
    # Files not named in fixes are untouched.
    assert other.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'unrelated.srt.bak').exists()


def test_main_srt_dry_run_leaves_file_untouched(tmp_path, monkeypatch,
                                                capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT_THREE, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.srt',
         'start': '00:00:01.000', 'replacement': 'New bar'},
        {'action': 'delete_line', 'file': 'EP001.srt', 'line': 10},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--dry-run') is None
    out = capsys.readouterr().out
    assert '[DRY-RUN] EP001.srt: 2 applied' in out
    assert '[DRY-RUN] 2 applied, 0 already correct, 0 skipped' in out

    assert path.read_text(encoding='utf-8') == SRT_THREE
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_main_srt_merge_cues(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT_THREE, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'merge_cues', 'file': 'EP001.srt',
                         'start': '00:00:01.000', 'count': 2}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    assert '1 applied' in capsys.readouterr().out
    assert path.read_text(encoding='utf-8') == (
        "1\n00:00:01,000 --> 00:00:04,000\nFoo bar\nFoo baz\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nBaz qux\n\n"
    )
    assert (tmp_path / 'EP001.srt.bak').exists()


def test_main_srt_unsupported_actions_are_skipped(tmp_path, monkeypatch,
                                                  capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_name', 'file': 'EP001.srt',
         'start': '00:00:01.000', 'replacement': 'X'},
        {'action': 'frobnicate', 'file': 'EP001.srt',
         'start': '00:00:01.000'},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert 'SRT 文件不支持 Name 字段替换' in out
    assert '未知 action: frobnicate' in out
    assert '0 applied, 0 already correct, 2 skipped' in out
    assert path.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_main_srt_missing_cue_is_skipped(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.srt',
         'start': '00:09:00.000', 'replacement': 'X'},
        {'action': 'delete_line', 'file': 'EP001.srt',
         'start': '00:09:00.000'},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '未找到 cue' in out
    assert '0 applied, 1 already correct, 1 skipped' in out
    assert path.read_text(encoding='utf-8') == SRT


# ═══════════════════════════════════════════════════════════════
# main() — global replacements
# ═══════════════════════════════════════════════════════════════

def _global_fix_fixture(tmp_path):
    for name in ('EP001.srt', 'EP002.srt'):
        (tmp_path / name).write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_global', 'original': 'Foo',
                         'replacement': 'Bar', 'note': '换字'}])
    return fixes


def test_main_global_replace_dry_run_does_not_touch_files(tmp_path,
                                                          monkeypatch,
                                                          capsys):
    fixes = _global_fix_fixture(tmp_path)
    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--dry-run') is None
    out = capsys.readouterr().out
    assert '[DRY-RUN] [OK] 换字 -> 影响 2 个文件' in out
    assert '[DRY-RUN] 1 applied, 0 already correct, 0 skipped' in out

    for name in ('EP001.srt', 'EP002.srt'):
        assert (tmp_path / name).read_text(encoding='utf-8') == SRT
        assert not (tmp_path / (name + '.bak')).exists()


def test_main_global_replace_writes_all_matching_files(tmp_path, monkeypatch,
                                                       capsys):
    fixes = _global_fix_fixture(tmp_path)
    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '[OK] 换字 -> 影响 2 个文件' in out
    assert '1 applied, 0 already correct, 0 skipped' in out

    for name in ('EP001.srt', 'EP002.srt'):
        assert 'Bar bar' in (tmp_path / name).read_text(encoding='utf-8')
        assert 'Foo bar' in (tmp_path / (name + '.bak')).read_text(
            encoding='utf-8')


def test_main_global_replace_no_match_is_reported_as_noop(tmp_path,
                                                          monkeypatch,
                                                          capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_global', 'original': 'Nope',
                         'replacement': 'X', 'note': '无命中'}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '[--] 无命中' in out and '无命中（1 个文件' in out
    assert '0 applied, 1 already correct, 0 skipped' in out
    assert path.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_main_global_fix_with_no_subtitle_files(tmp_path, monkeypatch,
                                                capsys):
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_global', 'original': 'Foo',
                         'replacement': 'Bar'}])
    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '[OK]' not in out
    assert '0 applied, 0 already correct, 0 skipped' in out


def test_main_global_regex_replaces_and_backs_up(tmp_path, monkeypatch,
                                                 capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_global_regex',
                         'pattern': r'F\w+', 'replacement': 'X'}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '1 applied, 0 already correct, 0 skipped' in out
    text = path.read_text(encoding='utf-8')
    assert 'X bar' in text and 'X baz' in text
    assert (tmp_path / 'EP001.srt.bak').exists()


# ═══════════════════════════════════════════════════════════════
# main() — style/comment fixes and ASS per-file dispatch
# ═══════════════════════════════════════════════════════════════

def test_main_style_and_comment_fixes_write_ass(tmp_path, monkeypatch,
                                                capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    srt = tmp_path / 'EP001.srt'
    srt.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'delete_style', 'style': 'Sign'},
        {'action': 'delete_comment', 'keyword': 'Translated by'},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '2 applied, 0 already correct, 0 skipped' in out
    assert out.count('影响 1 个文件') == 2

    content = ass.read_text(encoding='utf-8')
    assert '标识' not in content
    assert 'Translated by' not in content
    assert '旧甲' in content
    assert (tmp_path / 'EP001.ass.bak').read_text(
        encoding='utf-8') == ASS_TEXT
    # SRT has no styles/comments: the style pass must skip it.
    assert srt.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_main_style_fix_dry_run_does_not_write(tmp_path, monkeypatch,
                                               capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'delete_style', 'style': 'Sign'}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--dry-run') is None
    out = capsys.readouterr().out
    assert '[DRY-RUN] 1 applied, 0 already correct, 0 skipped' in out
    assert ass.read_text(encoding='utf-8') == ASS_TEXT
    assert not (tmp_path / 'EP001.ass.bak').exists()


def test_main_style_fix_with_no_match_is_noop(tmp_path, monkeypatch, capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'delete_style', 'style': 'NoSuchStyle'}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert '0 applied, 0 already correct, 0 skipped' in out
    assert ass.read_text(encoding='utf-8') == ASS_TEXT
    assert not (tmp_path / 'EP001.ass.bak').exists()


def test_main_ass_replace_text_name_and_delete(tmp_path, monkeypatch,
                                               capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.ass',
         'start': '0:00:01.00', 'replacement': '新甲'},
        # Same timecode as the Name fix below: already correct → no entry.
        {'action': 'replace_text', 'file': 'EP001.ass',
         'start': '0:00:03.00', 'replacement': '旧乙'},
        {'action': 'replace_name', 'file': 'EP001.ass',
         'line': 11, 'replacement': 'Bob'},
        {'action': 'delete_line', 'file': 'EP001.ass',
         'start': '0:00:05.00'},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert 'EP001.ass: 3 applied' in out
    assert '3 applied, 1 already correct, 0 skipped' in out

    content = ass.read_text(encoding='utf-8')
    assert '新甲' in content
    assert '旧乙' in content          # no-op replace kept the text
    assert 'Bob' in content           # Name field rewritten
    assert '标识' not in content      # delete_line removed the Sign cue
    assert 'Comment:' in content      # unrelated comment untouched
    assert (tmp_path / 'EP001.ass.bak').exists()


def test_main_ass_per_file_dry_run_does_not_write(tmp_path, monkeypatch,
                                                  capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_text', 'file': 'EP001.ass',
                         'start': '0:00:01.00', 'replacement': '新甲'}])

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--dry-run') is None
    out = capsys.readouterr().out
    assert '[DRY-RUN] EP001.ass: 1 applied' in out
    assert '[DRY-RUN] 1 applied, 0 already correct, 0 skipped' in out
    assert ass.read_text(encoding='utf-8') == ASS_TEXT
    assert not (tmp_path / 'EP001.ass.bak').exists()


def test_main_ass_missing_or_non_dialogue_targets_are_skipped(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.ass',
         'start': '0:00:09.00', 'replacement': 'x'},
        {'action': 'replace_text', 'file': 'EP001.ass',
         'line': 1, 'replacement': 'x'},
        {'action': 'delete_line', 'file': 'EP001.ass',
         'start': '0:00:09.00'},
        {'action': 'delete_line', 'file': 'EP001.ass', 'line': 1},
        {'action': 'replace_text', 'file': 'EP001.ass', 'line': 999,
         'replacement': 'x'},
        {'action': 'merge_cues', 'file': 'EP001.ass',
         'start': '0:00:01.00', 'count': 2},
    ])

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--fixes', fixes) is None
    out = capsys.readouterr().out
    assert 'start 时间码在文件中无匹配 Dialogue 行' in out
    assert '第 1 行不是 Dialogue 行' in out
    assert '未找到 cue（999）' in out
    assert 'ASS 不支持 action: merge_cues' in out
    assert '0 applied, 0 already correct, 6 skipped' in out
    assert ass.read_text(encoding='utf-8') == ASS_TEXT
    assert not (tmp_path / 'EP001.ass.bak').exists()


# ═══════════════════════════════════════════════════════════════
# main() — --log-to-report
# ═══════════════════════════════════════════════════════════════

def _capture_report(monkeypatch):
    calls = []

    def fake_upsert(path, step, entries):
        calls.append((path, step, entries))

    monkeypatch.setattr(update_report, 'upsert_entries', fake_upsert)
    return calls


def test_main_srt_logs_report_entries(tmp_path, monkeypatch, capsys):
    for name in ('EP001.srt', 'EP002.srt'):
        (tmp_path / name).write_text(SRT_THREE, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.srt',
         'start': '00:00:01.000', 'replacement': '新文本'},
        {'action': 'delete_line', 'file': 'EP001.srt', 'line': 10},
        {'action': 'merge_cues', 'file': 'EP002.srt',
         'start': '00:00:01.000', 'count': 2},
        # Trailing no-op keeps the per-fix loop going after the merge entry.
        {'action': 'replace_text', 'file': 'EP002.srt',
         'start': '00:00:05.000', 'replacement': 'Baz qux'},
    ])
    report = tmp_path / 'report.md'
    calls = _capture_report(monkeypatch)

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--log-to-report', report, '--step', '4') is None
    out = capsys.readouterr().out
    assert '3 entries logged to report layer 4' in out

    assert len(calls) == 1
    path_arg, step_arg, entries = calls[0]
    assert path_arg == str(report)
    assert step_arg == '4'
    by_corrected = {e['corrected']: e for e in entries}
    assert set(by_corrected) == {'新文本', '(已删除)', '合并 2 个 cues'}

    replaced = by_corrected['新文本']
    assert replaced['ep'] == 'EP001'
    assert replaced['time'] == '00:00:01.000'
    assert replaced['original'] == 'Foo bar'
    assert replaced['status'] == '✅'
    assert by_corrected['(已删除)']['status'] == '🗑️'
    assert by_corrected['(已删除)']['original'] == 'Baz qux'
    assert by_corrected['合并 2 个 cues']['ep'] == 'EP002'


def test_main_ass_logs_report_entries(tmp_path, monkeypatch, capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TEXT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [
        {'action': 'replace_text', 'file': 'EP001.ass',
         'start': '0:00:01.00', 'replacement': '新甲'},
        {'action': 'delete_line', 'file': 'EP001.ass',
         'start': '0:00:03.00'},
        # Applied but not report-logged: exercises the report-chain fallthrough.
        {'action': 'replace_name', 'file': 'EP001.ass',
         'line': 10, 'replacement': 'Alice'},
    ])
    report = tmp_path / 'report.md'
    calls = _capture_report(monkeypatch)

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--log-to-report', report, '--step', '4') is None
    assert '2 entries logged to report layer 4' in capsys.readouterr().out

    assert len(calls) == 1
    entries = calls[0][2]
    by_corrected = {e['corrected']: e for e in entries}
    assert set(by_corrected) == {'新甲', '(已删除)'}
    replaced = by_corrected['新甲']
    assert replaced['ep'] == 'EP001'
    assert replaced['time'] == '0:00:01.00'
    assert replaced['original'] == '旧甲'
    assert replaced['status'] == '✅'
    assert by_corrected['(已删除)']['original'] == '旧乙'


def test_main_dry_run_does_not_write_report(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    fixes = tmp_path / 'fixes.json'
    _write_json(fixes, [{'action': 'replace_text', 'file': 'EP001.srt',
                         'start': '00:00:01.000', 'replacement': 'New'}])
    report = tmp_path / 'report.md'
    calls = _capture_report(monkeypatch)

    assert _invoke(monkeypatch, '--target-dir', tmp_path, '--fixes', fixes,
                   '--dry-run', '--log-to-report', report,
                   '--step', '4') is None
    out = capsys.readouterr().out
    assert '1 entries would be logged to report layer 4' in out
    assert 'report not modified' in out
    assert calls == []
    assert path.read_text(encoding='utf-8') == SRT
    assert not report.exists()


# ═══════════════════════════════════════════════════════════════
# main() — 繁→简 wiring (--lang zh / --trad-to-simp)
# ═══════════════════════════════════════════════════════════════

def test_main_lang_zh_auto_converts_trad_to_simp(tmp_path, monkeypatch,
                                                 capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TRAD, encoding='utf-8')

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--lang', 'zh') is None
    captured = capsys.readouterr()
    assert '[trad→simp] 繁→简转换' in captured.out
    assert '[trad→simp] done: 1 changed' in captured.err

    content = ass.read_text(encoding='utf-8')
    assert '繁体字幕' in content
    assert 'Title: 繁體' in content  # metadata is not dialogue
    assert 'Comment: ' in content and '繁體註' in content  # comments skipped
    assert (tmp_path / 'EP001.ass.bak').exists()


def test_main_trad_to_simp_flag_converts_srt(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    original = "1\n00:00:01,000 --> 00:00:02,000\n繁體中文\n\n"
    path.write_text(original, encoding='utf-8')

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--trad-to-simp') is None
    captured = capsys.readouterr()
    assert '[trad→simp] done: 1 changed' in captured.err
    content = path.read_text(encoding='utf-8')
    assert '繁体中文' in content
    assert (tmp_path / 'EP001.srt.bak').exists()


def test_main_trad_to_simp_respects_dry_run(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'EP001.srt'
    original = "1\n00:00:01,000 --> 00:00:02,000\n繁體中文\n\n"
    path.write_text(original, encoding='utf-8')

    assert _invoke(monkeypatch, '--target-dir', tmp_path,
                   '--trad-to-simp', '--dry-run') is None
    captured = capsys.readouterr()
    assert 'preview: 1 changed' in captured.err
    assert path.read_text(encoding='utf-8') == original
    assert not (tmp_path / 'EP001.srt.bak').exists()


# ═══════════════════════════════════════════════════════════════
# _run_trad_to_simp / _run_degloss
# ═══════════════════════════════════════════════════════════════

def test_run_trad_to_simp_converts_ass_dialogue_only(tmp_path, capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TRAD, encoding='utf-8')

    af._run_trad_to_simp(str(tmp_path))
    err = capsys.readouterr().err
    assert 'EP001.ass: 1 changed' in err
    assert '[trad→simp] done: 1 changed' in err

    content = ass.read_text(encoding='utf-8')
    assert '繁体字幕' in content
    assert 'Comment: ' in content and '繁體註' in content
    assert (tmp_path / 'EP001.ass.bak').read_text(
        encoding='utf-8') == ASS_TRAD


def test_run_trad_to_simp_dry_run_does_not_touch_file(tmp_path, capsys):
    ass = tmp_path / 'EP001.ass'
    ass.write_text(ASS_TRAD, encoding='utf-8')

    af._run_trad_to_simp(str(tmp_path), dry_run=True)
    err = capsys.readouterr().err
    assert '[DRY-RUN] EP001.ass: 1 changed' in err
    assert '[trad→simp] preview: 1 changed' in err
    assert ass.read_text(encoding='utf-8') == ASS_TRAD
    assert not (tmp_path / 'EP001.ass.bak').exists()


def test_run_trad_to_simp_converts_srt_text(tmp_path, capsys):
    """Regression: SRT files used to be skipped entirely (0 changed)."""
    srt = tmp_path / 'EP001.srt'
    original = "1\n00:00:01,000 --> 00:00:02,000\n繁體中文\n\n"
    srt.write_text(original, encoding='utf-8')

    af._run_trad_to_simp(str(tmp_path))
    err = capsys.readouterr().err
    assert 'EP001.srt: 1 changed' in err
    assert '[trad→simp] done: 1 changed' in err
    assert '繁体中文' in srt.read_text(encoding='utf-8')
    assert (tmp_path / 'EP001.srt.bak').read_text(
        encoding='utf-8') == original


def test_run_trad_to_simp_no_changes(tmp_path, capsys):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(SRT, encoding='utf-8')

    af._run_trad_to_simp(str(tmp_path))
    err = capsys.readouterr().err
    assert '[trad→simp] done: 0 changed' in err
    assert srt.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()


def test_run_degloss_rewrites_translationese(tmp_path, capsys):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n他进行了调查\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n她迅速地离开\n\n",
        encoding='utf-8')
    ass = tmp_path / 'EP002.ass'
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,他进行了调查\n",
        encoding='utf-8')

    af._run_degloss(str(tmp_path))
    err = capsys.readouterr().err
    assert '[degloss] done: 3 changed' in err

    srt_text = srt.read_text(encoding='utf-8')
    assert '他调查了' in srt_text
    assert '她很快离开' in srt_text
    assert '他进行了调查' not in srt_text
    assert '他调查了' in ass.read_text(encoding='utf-8')


def test_run_degloss_dry_run_does_not_write(tmp_path, capsys):
    srt = tmp_path / 'EP001.srt'
    original = "1\n00:00:01,000 --> 00:00:02,000\n他进行了调查\n\n"
    srt.write_text(original, encoding='utf-8')

    af._run_degloss(str(tmp_path), dry_run=True)
    err = capsys.readouterr().err
    assert '[DRY-RUN] EP001.srt: 1 changed' in err
    assert '[degloss] preview: 1 changed' in err
    assert srt.read_text(encoding='utf-8') == original


def test_run_degloss_no_match_leaves_file_alone(tmp_path, capsys):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(SRT, encoding='utf-8')

    af._run_degloss(str(tmp_path))
    err = capsys.readouterr().err
    assert '[degloss] done: 0 changed' in err
    assert srt.read_text(encoding='utf-8') == SRT
    assert not (tmp_path / 'EP001.srt.bak').exists()
