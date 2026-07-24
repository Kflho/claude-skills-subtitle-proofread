"""Tests for fix/subtitle_session.py — static helpers and path resolution."""

import json
import os
import pytest
from fix.subtitle_session import SubtitleSession


class TestFindCueIndex:
    def test_basic(self):
        cues = [
            {'start': '00:00:01.000', 'text': 'first'},
            {'start': '00:00:05,000', 'text': 'second'},
        ]
        assert SubtitleSession.find_cue_index(cues, '00:00:01.000') == 0
        assert SubtitleSession.find_cue_index(cues, '00:00:05.000') == 1

    def test_comma_tolerant(self):
        cues = [{'start': '00:00:01,000', 'text': 'x'}]
        assert SubtitleSession.find_cue_index(cues, '00:00:01.000') == 0

    def test_not_found(self):
        cues = [{'start': '00:00:01.000', 'text': 'first'}]
        assert SubtitleSession.find_cue_index(cues, '00:00:99.000') is None


class TestFindClusterForTimecode:
    def test_found(self):
        clusters = [{
            'garbled': [
                {'start': '00:00:01.000', 'end': '00:00:02.000', 'text': 'bad'},
                {'start': '00:00:03.000', 'end': '00:00:04.000', 'text': 'bad2'},
            ]
        }]
        result = SubtitleSession.find_cluster_for_timecode(
            clusters, '00:00:03.000')
        assert result is not None

    def test_not_found(self):
        clusters = [{
            'garbled': [{'start': '00:00:01.000', 'text': 'bad'}]
        }]
        assert SubtitleSession.find_cluster_for_timecode(
            clusters, '00:00:99.000') is None

    def test_empty_clusters(self):
        assert SubtitleSession.find_cluster_for_timecode(
            [], '00:00:01.000') is None


class TestComputeReviewClipBounds:
    def test_no_speech_segments(self):
        result = SubtitleSession.compute_review_clip_bounds(
            10.0, 15.0, 5.0, 20.0, [], max_pad=2.0)
        assert result is not None
        clip_start, clip_end = result
        assert clip_start >= 5.0  # clamped to cluster_ss
        assert clip_end <= 20.0  # clamped to cluster_es

    def test_speech_within_garbled_region(self):
        speech_segs = [(10.5, 14.5)]
        result = SubtitleSession.compute_review_clip_bounds(
            10.0, 15.0, 5.0, 20.0, speech_segs, max_pad=2.0)
        assert result is not None
        clip_start, clip_end = result
        assert clip_start <= 10.5
        assert clip_end >= 14.5

    def test_no_speech_overlap_returns_none(self):
        speech_segs = [(0.0, 1.0), (30.0, 31.0)]  # far from garbled region
        result = SubtitleSession.compute_review_clip_bounds(
            10.0, 15.0, 5.0, 20.0, speech_segs, max_pad=2.0)
        assert result is None  # no speech detected → auto-cut


class TestTrimLeadingSilence:
    def test_trims_when_silence_exceeds_pad(self):
        speech_segs = [(10.0, 12.0)]
        result = SubtitleSession._trim_leading_silence(
            2.0, 15.0, 2.0, speech_segs)
        assert result == 8.0  # first_speech - max_pad

    def test_no_trim_when_silence_within_pad(self):
        speech_segs = [(3.0, 12.0)]
        result = SubtitleSession._trim_leading_silence(
            2.0, 15.0, 2.0, speech_segs)
        assert result == 2.0  # silence (2→3) = 1s < max_pad(2s)


class TestTrimTrailingSilence:
    def test_trims_when_silence_exceeds_pad(self):
        speech_segs = [(10.0, 12.0)]
        result = SubtitleSession._trim_trailing_silence(
            3.0, 20.0, 2.0, speech_segs)
        assert result == 14.0  # last_speech + max_pad

    def test_no_trim_when_silence_within_pad(self):
        speech_segs = [(10.0, 19.0)]
        result = SubtitleSession._trim_trailing_silence(
            3.0, 20.0, 2.0, speech_segs)
        assert result == 20.0


class TestSubtitleSessionInit:
    def test_basic_construction(self, tmp_path):
        """Verify SubtitleSession can be constructed."""
        s = SubtitleSession('EP001', str(tmp_path))
        assert s.episode == 'EP001'
        assert s.project_dir == str(tmp_path)
        assert s.target_lang == 'ja'

    def test_srt_path_resolution(self, tmp_path):
        """When SRT exists, it should be found."""
        srt_dir = tmp_path / 'AI审查后'
        srt_dir.mkdir()
        (srt_dir / 'test_EP001.srt').write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nHello\n\n", encoding='utf-8')
        s = SubtitleSession('EP001', str(tmp_path))
        if s.srt_path:
            assert 'EP001' in s.srt_path
