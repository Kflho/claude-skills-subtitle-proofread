"""Tests for fix/whisper_pipeline.py — VAD regions and Whisper matching."""

import json
import sys
import types
import wave

from fix import whisper_pipeline as wp


# ═══════════════════════════════════════════════════════════════
# Audio fixtures (real 16k mono 16-bit WAVs, no ffmpeg)
# ═══════════════════════════════════════════════════════════════

def _write_silence_wav(path, seconds, rate=16000):
    """Write an exact-length silent WAV."""
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b'\x00\x00' * int(round(seconds * rate)))


def _write_frame_wav(path, pattern, rate=16000, frame_ms=30, channels=1):
    """Write a WAV whose per-frame loudness follows ``pattern`` (True=loud).

    Timestamps therefore come out as ``frame_index * frame_ms / 1000``.
    """
    frame_samples = int(rate * frame_ms / 1000)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        loud = (1000).to_bytes(2, 'little', signed=True) * frame_samples * channels
        quiet = b'\x00' * 2 * frame_samples * channels
        for frame_loud in pattern:
            wf.writeframes(loud if frame_loud else quiet)


def _install_fake_webrtcvad(monkeypatch):
    """A fake webrtcvad whose is_speech mirrors the frame's loudness."""
    mod = types.ModuleType('webrtcvad')

    class FakeVad:
        def __init__(self, aggressiveness):
            self.aggressiveness = aggressiveness

        def is_speech(self, frame, rate):
            return any(frame)

    mod.Vad = FakeVad
    monkeypatch.setitem(sys.modules, 'webrtcvad', mod)


def cue(start, end, text='x', garbled=False, line=1):
    return {'start': f'{start:09.3f}', 'end': f'{end:09.3f}',
            'start_s': float(start), 'end_s': float(end),
            'text': text, 'is_garbled': garbled, 'line': line}


# ═══════════════════════════════════════════════════════════════
# VAD segment helpers
# ═══════════════════════════════════════════════════════════════

def test_merge_nearby_segments():
    merged = wp._merge_nearby_segments([(0, 1), (1.3, 2), (4, 5)])
    assert merged == [(0, 2), (4, 5)]
    assert wp._merge_nearby_segments([]) == []


def test_cue_overlaps_speech():
    segs = [(4.0, 6.0)]
    assert wp.cue_overlaps_speech(cue(1, 5), segs) is True
    assert wp.cue_overlaps_speech(cue(5.5, 6.0), segs) is True
    assert wp.cue_overlaps_speech(cue(6.1, 7.0), segs) is False
    assert wp.cue_overlaps_speech(cue(1, 4.5), segs,
                                  min_overlap_s=2.0) is False


def test_non_dialogue_markers():
    assert wp.is_non_dialogue_marker('[音楽]') is True
    assert wp.is_non_dialogue_marker('  [拍手] ') is True
    assert wp.is_non_dialogue_marker('こんにちは') is False


# ═══════════════════════════════════════════════════════════════
# Fix regions
# ═══════════════════════════════════════════════════════════════

def test_build_fix_regions_garbled_missing_and_clean():
    cues = [cue(0, 5, 'clean'), cue(10, 12, 'bad', garbled=True),
            cue(60, 70, 'clean')]
    regions = wp.build_fix_regions([(0, 5), (9.5, 12.5), (40, 42)], cues)

    garbled = [r for r in regions if r['type'] == 'garbled']
    assert garbled and garbled[0]['start_s'] <= 9.5
    assert [c['text'] for c in garbled[0]['cues_to_clear']] == ['bad']

    missing = [r for r in regions if r['type'] == 'missing']
    assert missing and missing[0]['start_s'] == 40
    assert missing[0]['cues_to_clear'] == []


def test_build_fix_regions_skips_speech_covered_by_clean_cues():
    cues = [cue(0, 5, 'clean')]
    assert wp.build_fix_regions([(0.5, 4.5)], cues) == []


def test_build_fix_regions_skips_long_and_out_of_range_speech():
    cues = [cue(0, 5, 'clean'), cue(10, 12, 'bad', garbled=True),
            cue(60, 70, 'clean')]
    # (0, 50) is longer than FIX_REGION_MAX_GAP_S once clamped to 0..70;
    # (100, 110) lies outside the subtitle range entirely.
    regions = wp.build_fix_regions([(0, 50), (100, 110)], cues)
    assert all(r['type'] != 'garbled' for r in regions)


def test_regions_to_clusters_drops_missing_and_expands_context(capsys):
    region = {
        'start_s': 10, 'end_s': 12, 'type': 'garbled',
        'cues_to_clear': [cue(10, 12, 'bad', garbled=True)],
        'context_left': cue(7, 9, 'left clean'),
        'context_right': cue(14, 16, 'right clean'),
    }
    missing = {'start_s': 30, 'end_s': 32, 'type': 'missing',
               'cues_to_clear': []}
    clusters = wp.regions_to_clusters([region, missing])

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster['ss'] == 7 and cluster['es'] == 16
    assert cluster['left_text'] == 'left clean'
    assert cluster['right_text'] == 'right clean'
    assert '无字幕覆盖' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Whisper ↔ cue matching
# ═══════════════════════════════════════════════════════════════

def _cluster():
    return {
        'ss': 10.0,
        'garbled': [cue(10, 12, 'bad', garbled=True)],
        'left_text': '', 'right_text': '',
    }


def test_match_whisper_to_cues_round1():
    cluster = _cluster()
    fixes, covered = wp.match_whisper_to_cues(
        [{'start_s': 0.5, 'end_s': 1.5, 'text': 'good',
          'avg_logprob': -0.2, 'no_speech_prob': 0.01,
          'compression_ratio': 1.0}],
        cluster, offset=0.0)

    assert covered == {cluster['garbled'][0]['start']}
    assert len(fixes) == 1
    assert fixes[0]['replacement'] == 'good'
    assert fixes[0]['confidence'] == 'high'
    assert fixes[0]['avg_logprob'] == -0.2


def test_match_whisper_to_cues_uncovered():
    fixes, covered = wp.match_whisper_to_cues([], _cluster(), offset=0.0)
    assert covered == set()
    assert fixes[0]['replacement'] is None
    assert fixes[0]['confidence'] == 'none'


def test_is_context_text_detects_adjacent_clean_cue():
    assert wp._is_context_text('こんにちは、元気ですか',
                               'こんにちは、元気ですか？', '') is True
    assert wp._is_context_text('まったく別の台詞',
                               'こんにちは', 'さようなら') is False
    assert wp._is_context_text('x', '', '') is False


def test_cluster_garbled_cues_uses_clean_neighbours():
    cues = [
        cue(0, 2, 'left clean'),
        cue(5, 6, 'bad1', garbled=True),
        cue(8, 9, 'bad2', garbled=True),
        cue(12, 14, 'right clean'),
    ]
    clusters = wp._cluster_garbled_cues(cues)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert len(cluster['garbled']) == 2
    assert cluster['left_text'] == 'left clean'
    assert cluster['right_text'] == 'right clean'
    assert cluster['ss'] <= 0
    assert cluster['es'] >= 14


# ═══════════════════════════════════════════════════════════════
# VAD speech timeline (Tier 3 — fake webrtcvad)
# ═══════════════════════════════════════════════════════════════

def test_get_speech_timeline_webrtc_segments(monkeypatch, tmp_path):
    _install_fake_webrtcvad(monkeypatch)
    wav = tmp_path / 'speech.wav'
    _write_frame_wav(wav, [False] * 10 + [True] * 15 + [False] * 20)

    assert wp.get_speech_timeline(str(wav)) == [(0.3, 0.75)]


def test_get_speech_timeline_merges_short_gaps(monkeypatch, tmp_path):
    _install_fake_webrtcvad(monkeypatch)
    wav = tmp_path / 'speech.wav'
    _write_frame_wav(wav, [False] * 10 + [True] * 15 + [False] * 5
                      + [True] * 15 + [False] * 10)

    # 0.30–0.75 + 0.90–1.35 with a 0.15s gap → merged by VAD_MERGE_GAP_S (0.5)
    assert wp.get_speech_timeline(str(wav)) == [(0.3, 1.35)]


def test_get_speech_timeline_flushes_trailing_speech(monkeypatch, tmp_path):
    _install_fake_webrtcvad(monkeypatch)
    wav = tmp_path / 'speech.wav'
    _write_frame_wav(wav, [False] * 5 + [True] * 20)

    assert wp.get_speech_timeline(str(wav)) == [(0.15, 0.75)]


def test_get_speech_timeline_drops_too_short_speech(monkeypatch, tmp_path):
    _install_fake_webrtcvad(monkeypatch)
    wav = tmp_path / 'speech.wav'
    _write_frame_wav(wav, [False] * 5 + [True] * 5 + [False] * 10)

    # 5 frames = 150ms < VAD_MIN_SPEECH_S (300ms)
    assert wp.get_speech_timeline(str(wav)) == []


def test_get_speech_timeline_falls_back_without_webrtcvad(monkeypatch,
                                                         tmp_path):
    monkeypatch.setitem(sys.modules, 'webrtcvad', None)
    wav = tmp_path / 'speech.wav'
    _write_frame_wav(wav, [False] * 3)
    seen = {}

    def fake_fallback(path):
        seen['path'] = path
        return [(1.0, 2.0)]

    monkeypatch.setattr(wp, '_get_speech_timeline_silencedetect', fake_fallback)
    assert wp.get_speech_timeline(str(wav)) == [(1.0, 2.0)]
    assert seen['path'] == str(wav)


def test_get_speech_timeline_falls_back_on_wrong_wav_format(monkeypatch,
                                                           tmp_path):
    _install_fake_webrtcvad(monkeypatch)
    wav = tmp_path / 'stereo.wav'
    _write_frame_wav(wav, [True] * 20, channels=2)

    monkeypatch.setattr(wp, '_get_speech_timeline_silencedetect',
                        lambda path: [(9.0, 10.0)])
    assert wp.get_speech_timeline(str(wav)) == [(9.0, 10.0)]


# ═══════════════════════════════════════════════════════════════
# Tier 1 — combined-audio orchestration
# ═══════════════════════════════════════════════════════════════

def _cluster_for(cue_):
    return {'ss': cue_['start_s'], 'es': cue_['end_s'], 'garbled': [cue_],
            'left_text': '', 'right_text': ''}


def _patch_extract(monkeypatch):
    """Make extract_audio_wav write real WAVs of the requested duration."""
    def fake_extract(video, output, ss=None, duration=None, **kwargs):
        _write_silence_wav(output, duration or 1.0)
    monkeypatch.setattr(wp, 'extract_audio_wav', fake_extract)


def test_concat_wavs_offsets_and_silence(tmp_path):
    first = tmp_path / 'a.wav'
    second = tmp_path / 'b.wav'
    _write_silence_wav(first, 1.0)
    _write_silence_wav(second, 0.5)
    combined = tmp_path / 'combined.wav'

    offsets, total = wp._concat_wavs(
        [(0, str(first), {'ss': 10.0}), (1, str(second), {'ss': 20.0})],
        str(combined), silence_s=2.0)

    assert offsets == [(0, 0.0, 1.0, 10.0), (1, 3.0, 3.5, 20.0)]
    assert total == 3.5
    with wave.open(str(combined), 'rb') as wf:
        assert wf.getnframes() == 16000 + 32000 + 8000


def test_run_tier1_without_clusters_is_noop():
    assert wp.run_tier1('ep.mkv', [], [], 'cli', 'model', 'ja', '.') == ([], [])


def test_run_tier1_maps_combined_offsets(monkeypatch, tmp_path):
    cues = [cue(10, 12, 'bad0', garbled=True),
            cue(30, 31, 'bad1', garbled=True)]
    clusters = [_cluster_for(cues[0]), _cluster_for(cues[1])]
    _patch_extract(monkeypatch)
    monkeypatch.setattr(wp, 'run_whisper', lambda *a, **k: [
        # combined audio: cluster0 = 0.0–2.0s, 2s silence, cluster1 = 4.0–5.0s
        {'start_s': 0.5, 'end_s': 1.0, 'text': 'fixed0'},
        {'start_s': 4.2, 'end_s': 4.8, 'text': 'fixed1'},
    ])

    fixes, unmatched = wp.run_tier1(
        'ep.mkv', cues, clusters, 'cli', 'model', 'ja', str(tmp_path))

    assert unmatched == []
    assert [f['replacement'] for f in fixes] == ['fixed0', 'fixed1']
    assert [f['confidence'] for f in fixes] == ['high', 'high']
    assert [f['start'] for f in fixes] == [cues[0]['start'], cues[1]['start']]


def test_run_tier1_keeps_unmatched_when_whisper_is_silent(monkeypatch,
                                                          tmp_path):
    cues = [cue(10, 12, 'bad', garbled=True)]
    _patch_extract(monkeypatch)
    monkeypatch.setattr(wp, 'run_whisper', lambda *a, **k: [])

    fixes, unmatched = wp.run_tier1(
        'ep.mkv', cues, [_cluster_for(cues[0])], 'cli', 'm', 'ja',
        str(tmp_path))

    assert fixes == []
    assert [c['start'] for c in unmatched] == [cues[0]['start']]


def test_run_tier1_runs_demucs_once_on_combined_audio(monkeypatch, tmp_path):
    cues = [cue(10, 12, 'bad', garbled=True)]
    _patch_extract(monkeypatch)
    combined = str(tmp_path / 'combined.wav')
    calls = {}

    def fake_separate(audio, output_dir=None, python_exe=None):
        calls['separate'] = audio
        return audio + '.vocals.wav'

    def fake_whisper(audio, *args, **kwargs):
        calls['whisper'] = audio
        return [{'start_s': 0.5, 'end_s': 1.0, 'text': 'fixed'}]

    monkeypatch.setattr(wp, 'separate_vocals', fake_separate)
    monkeypatch.setattr(wp, 'run_whisper', fake_whisper)

    wp.run_tier1('ep.mkv', cues, [_cluster_for(cues[0])], 'cli', 'm', 'ja',
                 str(tmp_path), separate_vocals_flag=True)

    assert calls['separate'] == combined
    assert calls['whisper'] == combined + '.vocals.wav'


def test_run_tier1_keeps_combined_when_demucs_returns_same_path(monkeypatch,
                                                                tmp_path):
    cues = [cue(10, 12, 'bad', garbled=True)]
    _patch_extract(monkeypatch)
    combined = str(tmp_path / 'combined.wav')
    monkeypatch.setattr(wp, 'separate_vocals',
                        lambda audio, **kwargs: audio)
    seen = {}

    def fake_whisper(audio, *args, **kwargs):
        seen['audio'] = audio
        return []

    monkeypatch.setattr(wp, 'run_whisper', fake_whisper)

    wp.run_tier1('ep.mkv', cues, [_cluster_for(cues[0])], 'cli', 'm', 'ja',
                 str(tmp_path), separate_vocals_flag=True)

    assert seen['audio'] == combined


# ═══════════════════════════════════════════════════════════════
# Tier 2 — full episode + transcript sidecar
# ═══════════════════════════════════════════════════════════════

def test_run_tier2_aligns_and_saves_transcript(monkeypatch, tmp_path):
    cues = [cue(0, 4, 'bad', garbled=True), cue(10, 12, 'clean')]
    _patch_extract(monkeypatch)
    monkeypatch.setattr(wp, 'run_whisper', lambda *a, **k: [
        {'start_s': 0.0, 'end_s': 3.0, 'text': 'よくできた'},
        {'start_s': 9.0, 'end_s': 9.5, 'text': '   '},
    ])
    transcript = tmp_path / 'asr' / 'transcript.json'

    fixes, stats = wp.run_tier2('ep.mkv', cues, 'cli', 'model', 'ja',
                                str(tmp_path),
                                save_transcript_to=str(transcript))

    assert stats == {'fixed': 1, 'unmatched': 0, 'kept': 1}
    assert fixes[0]['replacement'] == 'よくできた'
    assert fixes[0]['confidence'] == 'high'
    loaded = json.loads(transcript.read_text(encoding='utf-8'))
    assert loaded == [{'start_s': 0.0, 'text': 'よくできた'}]


def test_run_tier2_without_whisper_output_reports_unmatched(monkeypatch,
                                                            tmp_path):
    cues = [cue(0, 4, 'bad', garbled=True), cue(5, 6, 'clean')]
    _patch_extract(monkeypatch)
    monkeypatch.setattr(wp, 'run_whisper', lambda *a, **k: [])

    fixes, stats = wp.run_tier2('ep.mkv', cues, 'cli', 'm', 'ja',
                                str(tmp_path))

    assert fixes == []
    assert stats == {'fixed': 0, 'unmatched': 1, 'kept': 0}


# ═══════════════════════════════════════════════════════════════
# align_and_fix — coverage thresholds and bad ASR text
# ═══════════════════════════════════════════════════════════════

def test_align_and_fix_high_and_retry_by_coverage():
    high = [cue(0, 10, 'bad', garbled=True)]
    fixes, _ = wp.align_and_fix(high, [{'start_s': 0.0, 'end_s': 7.5,
                                        'text': 'よくできた'}])
    assert fixes[0]['confidence'] == 'high'      # 0.75 ≥ HIGH (0.5)

    retry = [cue(0, 10, 'bad', garbled=True)]
    fixes, _ = wp.align_and_fix(retry, [{'start_s': 0.0, 'end_s': 4.0,
                                         'text': 'よくできた'}])
    assert fixes[0]['confidence'] == 'retry'     # 0.4 ≥ MIN (0.3)


def test_align_and_fix_low_coverage_stays_unmatched():
    cues = [cue(0, 10, 'bad', garbled=True)]
    fixes, stats = wp.align_and_fix(cues, [{'start_s': 0.0, 'end_s': 2.0,
                                            'text': 'よくできた'}])
    assert fixes[0]['replacement'] is None
    assert fixes[0]['confidence'] == 'none'
    assert stats['unmatched'] == 1


def test_align_and_fix_rejects_junk_asr_text():
    cues = [cue(0, 10, 'bad', garbled=True)]
    # Full coverage but ASR output is not valid subtitle text for ja
    fixes, stats = wp.align_and_fix(cues, [{'start_s': 0.0, 'end_s': 9.0,
                                            'text': '123'}])
    assert fixes[0]['replacement'] is None
    assert stats['unmatched'] == 1


def test_align_and_fix_leaves_clean_cues_alone():
    cues = [cue(0, 2, 'clean'), cue(3, 4, 'bad', garbled=True)]
    fixes, stats = wp.align_and_fix(cues, [{'start_s': 3.0, 'end_s': 4.0,
                                            'text': 'よくできた'}])
    assert [f['start'] for f in fixes] == [cues[1]['start']]
    assert stats['kept'] == 1


def test_align_and_fix_survives_unwritable_transcript(tmp_path, capsys):
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    fixes, _ = wp.align_and_fix(
        [cue(0, 1, 'bad', garbled=True)], [],
        save_transcript_to=str(blocker / 'sub' / 'transcript.json'))
    assert fixes[0]['confidence'] == 'none'
    assert 'Failed to save whisper transcript' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# main() — VAD gate, tier selection, dry-run policy
# ═══════════════════════════════════════════════════════════════

def _main_argv(tmp_path, extra=()):
    return ['whisper_pipeline', 'ep.mkv', 'ep.srt',
            '--whisper-cli', 'cli', '--model', 'model',
            '--output', str(tmp_path / 'report.json'), *extra]


def _stub_scan(monkeypatch, cues):
    monkeypatch.setattr(wp, 'parse_srt', lambda path: cues)
    monkeypatch.setattr(wp, 'extract_audio_wav', lambda *a, **k: None)


def test_main_vad_delete_only_when_flagged(monkeypatch, tmp_path):
    cues = [cue(0, 1, 'bad', garbled=True)]
    applies = []

    def fake_vad(audio, cs, path, apply=False, aggressiveness=2):
        applies.append(apply)
        return [], cs, []

    _stub_scan(monkeypatch, cues)
    monkeypatch.setattr(wp, 'vad_delete_nonspeech', fake_vad)
    monkeypatch.setattr(wp, 'run_tier1', lambda *a, **k: ([], []))
    base = _main_argv(tmp_path)

    monkeypatch.setattr(sys, 'argv', base)
    wp.main()
    monkeypatch.setattr(sys, 'argv', base + ['--vad-clean-apply'])
    wp.main()
    # --dry-run overrides --vad-clean-apply: never write during preview
    monkeypatch.setattr(sys, 'argv',
                        base + ['--vad-clean-apply', '--dry-run'])
    wp.main()

    assert applies == [False, True, False]


def test_main_upgrades_to_tier2_above_threshold(monkeypatch, tmp_path):
    cues = [cue(0, 1, 'bad1', garbled=True),
            cue(2, 3, 'bad2', garbled=True)]
    monkeypatch.setattr(wp, 'UPGRADE_THRESHOLD', 1)
    tiers = []

    def fake_tier2(*args, **kwargs):
        tiers.append('tier2')
        return [], {'fixed': 0, 'unmatched': 2, 'kept': 0}

    _stub_scan(monkeypatch, cues)
    monkeypatch.setattr(wp, 'run_tier2', fake_tier2)
    monkeypatch.setattr(wp, 'run_tier1',
                        lambda *a, **k: tiers.append('tier1') or ([], []))

    monkeypatch.setattr(sys, 'argv',
                        _main_argv(tmp_path, ['--no-vad-clean', '--dry-run']))
    wp.main()

    assert tiers == ['tier2']
    report = json.loads((tmp_path / 'report.json').read_text(encoding='utf-8'))
    assert report['tier'] == 2
    assert report['garbled_cues'] == 2


def test_main_dry_run_does_not_touch_srt(monkeypatch, tmp_path):
    cues = [cue(0, 1, 'bad', garbled=True)]
    fixes = [{'start': cues[0]['start'], 'end': cues[0]['end'],
              'original': 'bad', 'replacement': 'good',
              'confidence': 'high', 'model': 'tier1'}]
    applied = []

    _stub_scan(monkeypatch, cues)
    monkeypatch.setattr(wp, 'run_tier1', lambda *a, **k: (fixes, []))
    monkeypatch.setattr(wp, 'apply_fixes_to_srt',
                        lambda path, fs: applied.append((path, len(fs))) or 1)

    monkeypatch.setattr(sys, 'argv',
                        _main_argv(tmp_path, ['--no-vad-clean', '--dry-run']))
    wp.main()
    assert applied == []

    monkeypatch.setattr(sys, 'argv',
                        _main_argv(tmp_path, ['--no-vad-clean']))
    wp.main()
    assert applied == [('ep.srt', 1)]

    report = json.loads((tmp_path / 'report.json').read_text(encoding='utf-8'))
    assert report['fixed'] == 1 and report['unmatched'] == 0
