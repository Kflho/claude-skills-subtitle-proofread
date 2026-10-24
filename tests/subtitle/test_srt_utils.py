"""Tests for lib/srt_utils.py — SRT cue building."""

from lib.srt_utils import build_srt_cue


class TestBuildSrtCue:
    """String formatting of a single SRT cue."""

    def test_basic_cue(self):
        d = {
            '_srt_index': 1,
            'start': '00:00:01.000',
            'end': '00:00:02.000',
            'text': 'test',
        }
        result = build_srt_cue(d)
        assert '1\n' in result
        assert '00:00:01,000 --> 00:00:02,000' in result
        assert 'test\n' in result

    def test_multiline_text(self):
        d = {
            '_srt_index': 5,
            'start': '00:01:00.500',
            'end': '00:01:05.000',
            'text': 'line1\nline2',
        }
        result = build_srt_cue(d)
        # Multiline text should be preserved
        assert 'line1\n' in result
        assert 'line2\n' in result

    def test_cue_with_empty_text(self):
        d = {
            '_srt_index': 1,
            'start': '00:00:01.000',
            'end': '00:00:02.000',
            'text': '',
        }
        result = build_srt_cue(d)
        # Should still produce valid SRT structure
        assert '1\n' in result
        assert '00:00:01,000 --> 00:00:02,000' in result

    def test_cue_with_index_only(self):
        """Minimal cue dict — only _srt_index."""
        d = {'_srt_index': 42, 'start': '', 'end': '', 'text': ''}
        result = build_srt_cue(d)
        assert '42\n' in result
