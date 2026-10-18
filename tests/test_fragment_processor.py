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


# ═══════════════════════════════════════════════════════════════
# VAD alignment — exact branches
# ═══════════════════════════════════════════════════════════════

def test_vad_align_one_segment_snaps_to_speech():
    fp = FragmentProcessor(MagicMock())
    cluster = {'ss': 5.0, 'es': 20.0, 'garbled': []}
    result = fp._vad_align_correction(cluster, [(10.0, 12.0)],
                                      'corrected', {'start_s': 10.0})
    assert result == {'start': '00:00:10,000', 'end': '00:00:12,000',
                      'text': 'corrected'}


def test_vad_align_segment_count_matches_garbled_cues():
    fp = FragmentProcessor(MagicMock())
    cluster = {'ss': 5.0, 'es': 20.0,
               'garbled': [{'start_s': 10.0}, {'start_s': 14.0}]}
    speech = [(10.0, 12.0), (14.0, 16.0)]

    result = fp._vad_align_correction(cluster, speech, 'text',
                                      {'start_s': 14.0})
    assert (result['start'], result['end']) == ('00:00:14,000',
                                                '00:00:16,000')


def test_vad_align_single_segment_always_snaps():
    fp = FragmentProcessor(MagicMock())
    cluster = {'ss': 5.0, 'es': 20.0,
               'garbled': [{'start_s': 10.0}, {'start_s': 11.0}]}
    # n == 1 short-circuits before the garbled-count check
    result = fp._vad_align_correction(cluster, [(14.0, 16.0)], 'text',
                                      {'start_s': 10.0})
    assert (result['start'], result['end']) == ('00:00:14,000',
                                                '00:00:16,000')


def test_vad_align_count_match_without_cue_match_falls_back():
    fp = FragmentProcessor(MagicMock())
    cluster = {'ss': 5.0, 'es': 20.0,
               'garbled': [{'start_s': 10.0}, {'start_s': 11.0}]}
    speech = [(14.0, 16.0), (18.0, 19.0)]
    # cue start matches no garbled cue (±0.5s) → cannot pick a segment
    assert fp._vad_align_correction(cluster, speech, 'text',
                                    {'start_s': 5.0}) is None


def test_vad_align_count_mismatch_falls_back():
    fp = FragmentProcessor(MagicMock())
    cluster = {'ss': 5.0, 'es': 20.0, 'garbled': [{'start_s': 10.0}]}
    speech = [(10.0, 12.0), (14.0, 16.0)]
    assert fp._vad_align_correction(cluster, speech, 'text',
                                    {'start_s': 10.0}) is None


# ═══════════════════════════════════════════════════════════════
# Whisper transcript context
# ═══════════════════════════════════════════════════════════════

def _write_transcript(session, segments):
    os.makedirs(session.temp_dir, exist_ok=True)
    path = os.path.join(session.temp_dir, 'whisper_full_EP001.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(segments, fh, ensure_ascii=False)
    return path


def test_whisper_context_missing_file(mock_session):
    fp = FragmentProcessor(mock_session)
    assert fp._extract_whisper_context(10.0, 20.0) == []


def test_whisper_context_window_filter(mock_session):
    _write_transcript(mock_session, [
        {'start_s': 1.0, 'text': 'a'},
        {'start_s': 15.0, 'text': 'b'},
        {'start_s': 100.0, 'text': 'c'},
    ])
    fp = FragmentProcessor(mock_session)

    ctx = fp._extract_whisper_context(20.0, 25.0, window_s=30.0)

    assert [c['text'] for c in ctx] == ['a', 'b']
    assert all(c['start_s'] <= 25.0 + 30.0 for c in ctx)


def test_whisper_context_caps_at_nearest_twenty(mock_session):
    segs = [{'start_s': float(i * 2), 'text': f's{i}'} for i in range(40)]
    _write_transcript(mock_session, segs)
    fp = FragmentProcessor(mock_session)

    ctx = fp._extract_whisper_context(40.0, 42.0, window_s=100.0)

    assert len(ctx) == 20
    assert ctx == sorted(ctx, key=lambda c: c['start_s'])


# ═══════════════════════════════════════════════════════════════
# Paired-cue mode
# ═══════════════════════════════════════════════════════════════

def test_build_paired_cues_prefers_clean_short_neighbour():
    fp = FragmentProcessor(MagicMock())
    cues = [
        {'start': '00:00:01,000', 'end': '00:00:02,000', 'start_s': 1.0,
         'text': 'target', 'is_garbled': True},
        {'start': '00:00:03,000', 'end': '00:00:04,000', 'start_s': 3.0,
         'text': 'a much longer garbled line', 'is_garbled': True},
        {'start': '00:00:04,000', 'end': '00:00:05,000', 'start_s': 4.0,
         'text': '短い', 'is_garbled': False},
        {'start': '00:00:20,000', 'end': '00:00:21,000', 'start_s': 20.0,
         'text': 'far away', 'is_garbled': False},
    ]
    fragment = {'start': '00:00:01,000', 'end': '00:00:02,000',
                'replacement': '修正'}

    paired = fp._build_paired_cues(cues, fragment)

    assert paired[0]['text'] == '短い'
    assert paired[0]['role'] == 'neighbor'
    assert paired[-1]['role'] == 'target'
    assert paired[-1]['text'] == '修正'
    assert all(c['text'] != 'far away' for c in paired)


def test_build_paired_cues_without_neighbour_returns_target_only():
    fp = FragmentProcessor(MagicMock())
    cues = [{'start': '00:00:01,000', 'end': '00:00:02,000',
             'start_s': 1.0, 'text': 'target', 'is_garbled': True}]
    paired = fp._build_paired_cues(cues, {'start': '00:00:01,000',
                                          'end': '00:00:02,000',
                                          'original': 'target'})
    assert [c['role'] for c in paired] == ['target']


# ═══════════════════════════════════════════════════════════════
# build_ai_fragments_json — reference/whisper/paired
# ═══════════════════════════════════════════════════════════════

def test_build_fragments_includes_whisper_and_reference(mock_session,
                                                        monkeypatch):
    mock_session.ref_srt_path = None
    monkeypatch.setattr(mock_session, 'find_ref_text',
                        lambda start, end: '参考文献', raising=False)
    _write_transcript(mock_session, [{'start_s': 11.0, 'text': 'whisper'}])
    fp = FragmentProcessor(mock_session)

    path = fp.build_ai_fragments_json([{
        'start': '00:00:11.000', 'end': '00:00:15.000',
        'original': 'x' * 300, 'replacement': 'good',
    }])

    frag = json.load(open(path, encoding='utf-8'))['fragments'][0]
    assert frag['original'] == 'x' * 200          # truncated
    assert frag['whisper_context'][0]['text'] == 'whisper'
    assert frag['reference_text'] == '参考文献'
    assert frag['correction'] == ''


def test_build_fragments_marks_paired_mode(mock_session):
    fp = FragmentProcessor(mock_session)

    path = fp.build_ai_fragments_json([{
        'start': '00:00:01.000', 'end': '00:00:05.000',
        'original': 'はい', 'replacement': 'おはようございます元気ですか',
    }])

    frag = json.load(open(path, encoding='utf-8'))['fragments'][0]
    assert frag['mode'] == 'paired'
    assert any(c['role'] == 'target' for c in frag['paired_cues'])


# ═══════════════════════════════════════════════════════════════
# apply_ai_fragments
# ═══════════════════════════════════════════════════════════════

def _write_fragments(session, fragments):
    os.makedirs(session.temp_dir, exist_ok=True)
    path = os.path.join(session.temp_dir, 'ai_fragments_EP001.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'episode': 'EP001', 'fragments': fragments}, fh,
                  ensure_ascii=False)
    return path


@pytest.fixture
def report_calls(monkeypatch):
    calls = []

    def fake_update(path, *, step, ep, time, corrected, status):
        calls.append({'path': path, 'step': step, 'time': time,
                      'corrected': corrected, 'status': status})
        return True

    monkeypatch.setattr('fix.fragment_processor.update_entry_status',
                        fake_update)
    return calls


def _read_cues(session):
    from lib.whisper_utils import parse_srt
    return parse_srt(session.srt_path, mark_garbled=False)


def test_apply_missing_or_empty_json(mock_session, report_calls):
    fp = FragmentProcessor(mock_session)
    assert fp.apply_ai_fragments() == 0

    path = _write_fragments(mock_session, [])
    assert fp.apply_ai_fragments(path) == 0


def test_apply_text_only_correction_writes_srt(mock_session, report_calls):
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'end': '00:00:05,000',
        'original': 'bad_text', 'correction': 'good text',
    }])

    assert fp.apply_ai_fragments(path) == 1

    cues = _read_cues(mock_session)
    assert cues[0]['text'] == 'good text'
    assert not os.path.exists(path)                 # temp JSON removed
    assert report_calls[-1]['status'] == '✅'
    assert report_calls[-1]['time'] == '00:00:01,000'


def test_apply_delete_removes_cue(mock_session, report_calls):
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:06,000', 'correction': '删除',
    }])

    assert fp.apply_ai_fragments(path) == 1
    assert len(_read_cues(mock_session)) == 2


def test_apply_unknown_timecode_is_skipped(mock_session, report_calls):
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:09:99,000', 'correction': 'x',
    }])

    assert fp.apply_ai_fragments(path) == 0


def test_apply_paired_neighbour_correction(mock_session, report_calls):
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': 'target fix',
        'mode': 'paired',
        'paired_cues': [
            {'start': '00:00:06,000', 'correction': 'neighbour fix',
             'role': 'neighbor'},
            {'start': '00:00:01,000', 'role': 'target', 'correction': ''},
        ],
    }])

    assert fp.apply_ai_fragments(path) == 2
    cues = _read_cues(mock_session)
    assert cues[0]['text'] == 'target fix'
    assert cues[1]['text'] == 'neighbour fix'


def test_apply_vad_alignment_snaps_timecodes(mock_session, report_calls):
    mock_session.video_path = 'v.mkv'
    mock_session.save_review_clusters([{
        'ss': 0.0, 'es': 5.0,
        'garbled': [{'start': '00:00:01,000', 'end': '00:00:05,000',
                     'text': 'bad_text', 'start_s': 1.0, 'end_s': 5.0}],
        'left_text': '', 'right_text': '',
    }])
    mock_session.save_speech_segs([(0.5, 3.0)])
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': 'aligned text',
    }])

    assert fp.apply_ai_fragments(path) == 1
    cue = _read_cues(mock_session)[0]
    assert (cue['start_s'], cue['end_s']) == (0.5, 3.0)
    assert cue['text'] == 'aligned text'


def test_apply_unfilled_meaningless_text_auto_cuts(mock_session, report_calls):
    mock_session.save_speech_segs([(0.0, 5.0)])
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': '', 'original': '。。。',
    }])

    assert fp.apply_ai_fragments(path) == 0
    assert all(c['start'] != '00:00:01,000' for c in _read_cues(mock_session))
    assert report_calls[-1]['status'] == '🗑️'


def test_apply_unfilled_without_speech_auto_cuts(mock_session, report_calls):
    mock_session.save_speech_segs([(30.0, 35.0)])
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': '', 'original': 'おはよう',
    }])

    fp.apply_ai_fragments(path)
    assert all(c['start'] != '00:00:01,000' for c in _read_cues(mock_session))
    assert report_calls[-1]['corrected'] == '(VAD无语音)'


def test_apply_unfilled_noise_context_auto_cuts(mock_session, report_calls):
    mock_session.save_speech_segs([(0.0, 5.0)])
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': '', 'original': 'おはよう',
        'context_before': ['。。。', '？？'],
        'context_after': ['！！'],
    }])

    fp.apply_ai_fragments(path)
    assert all(c['start'] != '00:00:01,000' for c in _read_cues(mock_session))
    assert report_calls[-1]['corrected'] == '(噪音包围)'


def test_apply_unfilled_with_speech_escalates_to_marker(mock_session,
                                                        report_calls):
    mock_session.save_speech_segs([(0.0, 5.0)])
    fp = FragmentProcessor(mock_session)
    path = _write_fragments(mock_session, [{
        'start': '00:00:01,000', 'correction': '', 'original': 'おはよう',
        'context_before': ['こんにちは', '元気ですか'],
        'context_after': ['さようなら'],
    }])

    fp.apply_ai_fragments(path)
    cues = _read_cues(mock_session)
    assert cues[0]['text'] == '[???]'
    assert report_calls[-1]['status'] == '⬜'
    assert report_calls[-1]['corrected'] == '[???]'
