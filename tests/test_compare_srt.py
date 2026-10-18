"""Tests for fix/compare_srt.py — time overlap, similarity, verdicts."""

from fix.compare_srt import compare, print_report, similarity, time_overlap


def cue(start, end, text):
    return {'start': f'00:00:{start:02d},000', 'end': f'00:00:{end:02d},000',
            'start_s': float(start), 'end_s': float(end), 'text': text}


def test_similarity_bounds():
    assert similarity('abc', 'abc') == 1.0
    assert similarity('abc', 'xyz') == 0.0
    assert similarity('', 'abc') == 0.0
    assert 0.0 < similarity('abc', 'abd') < 1.0
    assert similarity('ABC', 'abc') == 1.0      # case-insensitive


def test_time_overlap():
    a = cue(1, 5, 'a')
    assert time_overlap(a, cue(2, 4, 'b')) == 0.5   # normalized by a's length
    assert time_overlap(a, cue(1, 5, 'b')) == 1.0
    assert time_overlap(a, cue(6, 7, 'b')) == 0.0
    assert time_overlap(a, cue(3, 7, 'b')) == 0.5


def test_compare_match_suspicious_mismatch_and_no_reference():
    whisper = [
        cue(1, 3, 'おはよう'),
        cue(5, 7, 'こんにちは'),
        cue(9, 11, 'さようなら'),
        cue(20, 22, 'はじめまして'),
    ]
    ref = [
        cue(1, 3, 'おはよう'),
        cue(5, 7, 'こんばんは'),     # partial similarity
        cue(9, 11, 'まったく違う'),
    ]
    diffs = compare(whisper, ref, threshold=0.4)

    assert diffs[0]['verdict'] == 'match'
    assert diffs[1]['verdict'] in ('suspicious', 'mismatch')
    assert diffs[2]['verdict'] == 'mismatch'
    assert diffs[3]['reference'] == '(no match)'
    assert diffs[3]['verdict'] == 'mismatch'


def test_compare_picks_best_overlap_candidate():
    whisper = [cue(1, 5, 'はい')]
    ref = [cue(1, 2, 'はい'), cue(4, 6, 'はい')]
    diffs = compare(whisper, ref, threshold=0.4)
    assert len(diffs) == 1
    assert diffs[0]['reference'] == 'はい'
    assert diffs[0]['similarity'] == 1.0


def test_print_report_counts(capsys):
    diffs = [
        {'start': '00:00:01,000', 'whisper': 'a', 'reference': 'a',
         'similarity': 1.0, 'verdict': 'match'},
        {'start': '00:00:02,000', 'whisper': 'b', 'reference': 'c',
         'similarity': 0.5, 'verdict': 'suspicious'},
        {'start': '00:00:03,000', 'whisper': 'd', 'reference': '(no match)',
         'similarity': 0.0, 'verdict': 'mismatch'},
    ]
    print_report(diffs)
    out = capsys.readouterr().out
    assert 'Total: 3 cues' in out
    assert 'Match:      1' in out
    assert 'Suspicious: 1' in out
    assert 'Mismatch:   1' in out
    assert '[?]' in out and '[X]' in out
