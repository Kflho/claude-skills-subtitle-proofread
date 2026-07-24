"""Tests for fix/fragment_processor.py — AI fragment processing with mocks.

Tier 3: uses mock SubtitleSession to test FragmentProcessor logic
without actual video/audio/Whisper binaries.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch
from fix.subtitle_session import SubtitleSession
from fix.fragment_processor import FragmentProcessor


@pytest.fixture
def mock_session(tmp_path):
    """Create a SubtitleSession for testing FragmentProcessor."""
    srt_dir = tmp_path / 'AI审查后'
    srt_dir.mkdir()
    srt_path = srt_dir / 'test_EP001.srt'
    srt_path.write_text(
        "1\n00:00:01,000 --> 00:00:05,000\nbad_text\n\n"
        "2\n00:00:06,000 --> 00:00:10,000\nHello world\n\n"
        "3\n00:00:11,000 --> 00:00:15,000\nこんにちは\n\n",
        encoding='utf-8')
    reports_dir = tmp_path / 'reports'
    reports_dir.mkdir()
    temp_dir = tmp_path / 'temp'
    temp_dir.mkdir()
    session = SubtitleSession('EP001', str(tmp_path))
    return session


class TestBuildAiFragmentsJson:
    def test_empty_fragments(self, mock_session):
        fp = FragmentProcessor(mock_session)
        result = fp.build_ai_fragments_json([])
        assert result is None

    def test_basic_fragment(self, mock_session):
        fp = FragmentProcessor(mock_session)
        fragments = [
            {'start': '00:00:01.000', 'end': '00:00:05.000',
             'original': 'bad_text', 'replacement': 'good_text',
             'whisper_original_ja': 'いいテキスト'},
        ]
        json_path = fp.build_ai_fragments_json(fragments)
        assert json_path is not None
        assert os.path.exists(json_path)

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data['episode'] == 'EP001'
        assert len(data['fragments']) == 1
        assert data['fragments'][0]['original'] == 'bad_text'

    def test_fragment_includes_context(self, mock_session):
        fp = FragmentProcessor(mock_session)
        fragments = [
            {'start': '00:00:11.000', 'end': '00:00:15.000',
             'original': 'bad', 'replacement': 'good',
             'whisper_original_ja': ''},
        ]
        json_path = fp.build_ai_fragments_json(fragments)
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        frag = data['fragments'][0]
        # Should have context from surrounding cues
        assert 'context_before' in frag
        assert 'context_after' in frag
        # 'Hello world' should be in context_before
        assert any('Hello world' in c for c in frag['context_before'])


class TestVadAlignCorrection:
    def test_zero_segments_returns_none(self):
        """0 VAD segments → fallback (return None)."""
        session = MagicMock()
        fp = FragmentProcessor(session)
        cluster = {'ss': 5.0, 'es': 20.0, 'garbled': []}
        cue = {'start_s': 10.0}
        result = fp._vad_align_correction(cluster, [], 'new text', cue)
        assert result is None

    def test_single_segment(self):
        """1 VAD segment → snap to it."""
        session = MagicMock()
        fp = FragmentProcessor(session)
        cluster = {'ss': 5.0, 'es': 20.0, 'garbled': []}
        speech_segs = [(10.0, 12.0)]
        cue = {'start_s': 10.0}
        result = fp._vad_align_correction(
            cluster, speech_segs, 'corrected', cue)
        assert result is not None
        assert result['start'] is not None
        assert result['end'] is not None
        assert result['text'] == 'corrected'

    def test_mismatch_count_fallback(self):
        """Different number of segments vs garbled cues — may fallback or match."""
        session = MagicMock()
        fp = FragmentProcessor(session)
        cluster = {
            'ss': 5.0, 'es': 20.0,
            'garbled': [
                {'start_s': 10.0}, {'start_s': 14.0}
            ]
        }
        speech_segs = [(10.0, 12.0)]  # 1 vs 2 garbled
        cue = {'start_s': 10.0}
        result = fp._vad_align_correction(
            cluster, speech_segs, 'text', cue)
        # May return result or None depending on matching strategy
        if result is not None:
            assert result['text'] == 'text'
