"""Standalone Qwen3 CLI helpers (SRT/timecode), no model required."""

from asr.qwen3_transcribe import _srt_time, write_srt
from lib.asr.base import Segment


class TestSrtTime:
    def test_zero(self):
        assert _srt_time(0) == '00:00:00,000'

    def test_fraction(self):
        assert _srt_time(1.234) == '00:00:01,234'

    def test_hours(self):
        assert _srt_time(3661.5) == '01:01:01,500'

    def test_negative_clamped(self):
        assert _srt_time(-1) == '00:00:00,000'


class TestWriteSrt:
    def test_writes_cues(self, tmp_path):
        path = tmp_path / 'out.srt'
        write_srt([
            Segment(text='お前', start=0.0, end=1.0),
            Segment(text='何してんだよ', start=1.2, end=2.5),
        ], str(path))
        text = path.read_text(encoding='utf-8')
        assert '00:00:00,000 --> 00:00:01,000' in text
        assert '00:00:01,200 --> 00:00:02,500' in text
        assert 'お前' in text
        assert '何してんだよ' in text

    def test_skips_empty_text(self, tmp_path):
        path = tmp_path / 'out.srt'
        write_srt([
            Segment(text='', start=0.0, end=1.0),
            Segment(text='a', start=1.0, end=2.0),
        ], str(path))
        text = path.read_text(encoding='utf-8')
        assert text.startswith('1\n')
        assert 'a' in text
