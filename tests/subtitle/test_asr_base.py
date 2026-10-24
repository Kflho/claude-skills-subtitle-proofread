"""Unit tests for the engine-neutral ASR contracts."""

from lib.asr.base import (
    AlignResult, ASRConfig, ASRResult, Segment, Token, build_segments,
    language_name,
)


class TestToken:
    def test_roundtrip(self):
        token = Token(text='お前', start=1.234, end=1.567, speaker='S1', score=0.9)
        again = Token.from_dict(token.to_dict())
        assert again.text == 'お前'
        assert again.start == 1.234
        assert again.end == 1.567
        assert again.speaker == 'S1'
        assert again.score == 0.9

    def test_optional_fields_omitted(self):
        data = Token(text='a', start=1.0, end=2.0).to_dict()
        assert 'speaker' not in data
        assert 'score' not in data


class TestASRResult:
    def test_roundtrip(self):
        result = ASRResult(
            text='お前、何してんだよ！',
            language='Japanese',
            segments=[Segment(text='お前、何してんだよ！', start=1.0, end=2.5)],
            tokens=[Token(text='お前', start=1.0, end=1.2),
                    Token(text='何', start=1.3, end=1.4)],
            engine='qwen3',
            model='Qwen/Qwen3-ASR-1.7B',
            audio_duration=2.5,
            context_hash='abc123',
            meta={'timestamps': True},
        )
        again = ASRResult.from_dict(result.to_dict())
        assert again.text == result.text
        assert again.language == 'Japanese'
        assert len(again.segments) == 1
        assert len(again.tokens) == 2
        assert again.has_timestamps
        assert again.meta['timestamps'] is True

    def test_empty_result_has_no_timestamps(self):
        result = ASRResult(text='')
        assert not result.has_timestamps
        assert result.to_dict()['tokens'] == []


class TestAlignResult:
    def test_roundtrip(self):
        aligned = AlignResult(
            text='お前', language='Japanese',
            tokens=[Token(text='お前', start=0.5, end=0.8)],
            meta={'aligner': 'qwen3'},
        )
        again = AlignResult.from_dict(aligned.to_dict())
        assert again.text == 'お前'
        assert again.tokens[0].start == 0.5


class TestBuildSegments:
    def test_groups_on_gap(self):
        tokens = [
            Token('お前', 1.0, 1.2),
            Token('何', 1.3, 1.4),
            Token('してる', 3.0, 3.4),
        ]
        segs = build_segments(tokens, gap_s=0.8)
        assert len(segs) == 2
        assert segs[0].text == 'お前何'
        assert segs[0].start == 1.0
        assert segs[0].end == 1.4
        assert segs[1].text == 'してる'

    def test_groups_on_sentence_mark(self):
        tokens = [
            Token('行くぞ', 1.0, 1.4),
            Token('！', 1.4, 1.5),
            Token('待て', 1.5, 1.8),
        ]
        segs = build_segments(tokens, gap_s=5.0)
        assert len(segs) == 2
        assert segs[0].text == '行くぞ！'
        assert segs[1].text == '待て'

    def test_empty(self):
        assert build_segments([]) == []

    def test_sorts_tokens(self):
        tokens = [Token('後', 2.0, 2.2), Token('前', 1.0, 1.2)]
        segs = build_segments(tokens, gap_s=5.0)
        assert segs[0].text == '前後'


class TestASRConfig:
    def test_defaults(self):
        cfg = ASRConfig()
        assert cfg.model == ''
        assert cfg.device == 'cuda:0'
        assert cfg.dtype == 'bfloat16'
        assert cfg.request_timeout == 1800.0

    def test_option(self):
        cfg = ASRConfig(options={'whisper_model': 'x'})
        assert cfg.option('whisper_model') == 'x'
        assert cfg.option('missing', 'fallback') == 'fallback'


class TestLanguageName:
    def test_known_codes(self):
        assert language_name('ja') == 'Japanese'
        assert language_name('JA') == 'Japanese'
        assert language_name('Japanese') == 'Japanese'
        assert language_name('zh') == 'Chinese'

    def test_unknown_passthrough(self):
        assert language_name('ru') == 'ru'
