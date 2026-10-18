"""P0 evaluation metric tests (no audio/model required)."""

from types import SimpleNamespace

from eval.asr_eval import boundary_metrics, compute_cer, normalize_text


class TestNormalize:
    def test_strips_punctuation_and_spaces(self):
        assert normalize_text('こんにちは、世界！') == 'こんにちは世界'
        assert normalize_text('  a b  c  ') == 'abc'

    def test_nfkc(self):
        assert normalize_text('１２３ＡＢＣ') == '123ABC'


class TestCER:
    def test_identical(self):
        out = compute_cer('お前、何してんだよ！', 'お前何してんだよ')
        assert out['cer'] == 0.0
        assert out['distance'] == 0

    def test_substitution(self):
        out = compute_cer('あいう', 'あいえ')
        assert out['distance'] == 1
        assert out['substitutions'] == 1
        assert out['cer'] == 1 / 3

    def test_insertion(self):
        out = compute_cer('あい', 'あいう')
        assert out['distance'] == 1
        assert out['insertions'] == 1
        assert out['cer'] == 0.5

    def test_deletion(self):
        out = compute_cer('あいう', 'あい')
        assert out['distance'] == 1
        assert out['deletions'] == 1
        assert out['cer'] == 1 / 3

    def test_empty_reference(self):
        out = compute_cer('', 'あ')
        assert out['ref_chars'] == 0
        assert out['cer'] == 1.0


class TestBoundaryMetrics:
    def test_segment_overlap(self):
        ref = [
            {'start_s': 0.0, 'end_s': 2.0},
            {'start_s': 3.0, 'end_s': 4.0},
        ]
        segs = [
            SimpleNamespace(start=0.2, end=2.2),
            SimpleNamespace(start=3.0, end=4.0),
        ]
        out = boundary_metrics(ref, segs)
        assert out['cues'] == 2
        assert out['matched'] == 2
        assert out['missed'] == 0
        assert out['mae_ms'] == 100.0  # (200 + 0) / 2

    def test_missing_overlap(self):
        ref = [{'start_s': 10.0, 'end_s': 12.0}]
        segs = [SimpleNamespace(start=0.0, end=1.0)]
        out = boundary_metrics(ref, segs)
        assert out['missed'] == 1
        assert out['matched'] == 0
        assert out['mae_ms'] == 2000.0

    def test_empty(self):
        out = boundary_metrics([], [])
        assert out['cues'] == 0
        assert out['mae_ms'] is None
