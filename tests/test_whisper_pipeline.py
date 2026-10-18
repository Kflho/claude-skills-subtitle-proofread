"""Tests for fix/whisper_pipeline.py — VAD regions and Whisper matching."""

from fix import whisper_pipeline as wp


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
