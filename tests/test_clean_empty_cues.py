"""Tests for utils/clean_empty_cues.py — SRT/ASS empty cue removal."""

from utils.clean_empty_cues import clean_ass, clean_srt


SRT_WITH_EMPTY = (
    "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n\n"
    "3\n00:00:05,000 --> 00:00:06,000\nworld\n\n"
)


def _lines(text):
    return text.splitlines(keepends=True)


def test_clean_srt_removes_empty_and_renumbers():
    new_lines, removed = clean_srt(_lines(SRT_WITH_EMPTY))
    assert removed == 1
    body = ''.join(new_lines)
    assert 'hello' in body and 'world' in body
    assert '00:00:03,000' not in body
    # renumbered 1,2
    assert body.startswith('1\n')
    assert '\n2\n' in body


def test_clean_srt_without_empty_returns_input_unchanged():
    text = ("1\n00:00:01,000 --> 00:00:02,000\nhello\n\n")
    lines = _lines(text)
    new_lines, removed = clean_srt(lines)
    assert removed == 0
    assert new_lines is lines


def test_clean_ass_removes_only_empty_dialogue():
    lines = [
        '[Script Info]\n',
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello\n',
        'Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,\n',
        'Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,note\n',
    ]
    new_lines, removed = clean_ass(lines)
    assert removed == 1
    kept = ''.join(new_lines)
    assert 'hello' in kept
    assert 'Comment:' in kept
    assert '0:00:03.00' not in kept
    assert new_lines[0] == '[Script Info]\n'


def test_clean_ass_without_empty_keeps_everything():
    lines = ['Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello\n']
    new_lines, removed = clean_ass(lines)
    assert removed == 0
    assert new_lines == lines
