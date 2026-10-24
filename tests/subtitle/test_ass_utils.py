"""Tests for lib/ass_utils.py — ASS format parsing and building."""

import pytest
from lib.ass_utils import (
    time_to_ms, ms_to_time,
    strip_ass_tags,
    parse_dialogue, build_dialogue_line,
    parse_comment_line,
)


class TestTimeToMs:
    def test_basic(self):
        assert time_to_ms("0:01:23.45") == 83450

    def test_hour(self):
        assert time_to_ms("1:00:00.00") == 3600000

    def test_zero(self):
        assert time_to_ms("0:00:00.00") == 0


class TestMsToTime:
    def test_basic(self):
        result = ms_to_time(83450)
        assert "0:" in result
        assert "23.45" in result

    def test_hour(self):
        result = ms_to_time(3600000)
        assert "1:" in result
        assert "00.00" in result

    def test_srt_format(self):
        result = ms_to_time(83450, format='srt')
        assert result == "00:01:23,450"


class TestStripAssTags:
    def test_fade_tag(self):
        assert strip_ass_tags(r"{\fad(100,100)}hello") == "hello"

    def test_multiple_tags(self):
        assert strip_ass_tags(r"{\fad(100,100)\pos(50,50)}text") == "text"

    def test_html_entities(self):
        result = strip_ass_tags("&lt;i&gt;text&lt;/i&gt;")
        assert "text" in result

    def test_no_tags(self):
        assert strip_ass_tags("plain text") == "plain text"

    def test_empty(self):
        assert strip_ass_tags("") == ""


class TestParseDialogue:
    def test_valid(self):
        line = "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,Hello world"
        d = parse_dialogue(line)
        assert d is not None
        assert d['text'] == "Hello world"
        assert d['start'] == "0:00:01.00"

    def test_non_dialogue(self):
        assert parse_dialogue("Comment: 0,0:00:01.00,...") is None

    def test_script_info(self):
        assert parse_dialogue("[Script Info]") is None

    def test_empty_text(self):
        line = "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,"
        d = parse_dialogue(line)
        assert d is not None
        assert d['text'] == ""


class TestBuildDialogueLine:
    def test_roundtrip(self):
        original = "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,Hello"
        d = parse_dialogue(original)
        result = build_dialogue_line(d)
        assert "Hello" in result
        assert "0:00:01.00" in result

    def test_with_tags(self):
        d = {
            'layer': 0, 'start': '0:00:01.00', 'end': '0:00:05.00',
            'style': 'Default', 'name': '', 'margin_l': 0,
            'margin_r': 0, 'margin_v': 0, 'effect': '',
            'text': r'{\fad(100,100)}Hello',
        }
        result = build_dialogue_line(d)
        assert '{\\fad(100,100)}Hello' in result


class TestParseCommentLine:
    def test_valid(self):
        line = "Comment: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,a note"
        c = parse_comment_line(line)
        assert c is not None
        assert c['text'] == "a note"

    def test_non_comment(self):
        assert parse_comment_line("Dialogue: 0,...") is None
