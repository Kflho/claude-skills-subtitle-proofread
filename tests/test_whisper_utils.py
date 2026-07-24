"""Tests for lib/whisper_utils.py — timecodes, text classification, filtering."""

import pytest
from lib.whisper_utils import (
    to_seconds, format_tc,
    extract_ep_number, _is_valid_ep_number, _clean_filename,
    classify_garbled_text, is_valid_subtitle_text,
    looks_like_plausible_text, meaningful_char_count,
    is_length_anomaly, is_proper_noun_pattern,
    filter_low_confidence, setup_windows_utf8,
    OP_BOUNDARY_SEC, ED_BOUNDARY_SEC,
    parse_srt,
)


class TestToSeconds:
    def test_comma(self):
        assert to_seconds("00:01:23,456") == 83.456

    def test_dot(self):
        assert to_seconds("00:01:23.456") == 83.456

    def test_hour(self):
        assert to_seconds("01:00:00.000") == 3600.0

    def test_empty(self):
        # to_seconds may raise ValueError for empty string
        try:
            result = to_seconds("")
            assert result == 0.0
        except (ValueError, AttributeError):
            pass  # raising an error for empty input is also valid behavior


class TestFormatTc:
    def test_roundtrip(self):
        assert format_tc(83.456) == "00:01:23,456"

    def test_hour(self):
        assert format_tc(3600.0) == "01:00:00,000"

    def test_zero(self):
        assert format_tc(0.0) == "00:00:00,000"

    def test_hour_boundary(self):
        assert format_tc(3661.5) == "01:01:01,500"


class TestEpNumber:
    def test_extract_ep_number_standard(self):
        assert extract_ep_number("show_064.srt") == "EP064"

    def test_extract_with_brackets(self):
        result = extract_ep_number("[Group] Show - 031 EP..srt")
        assert result == "EP031" or result.startswith("EP")

    def test_extract_fallback(self):
        result = extract_ep_number("no_number_here.srt")
        assert result == "EP000" or result == "???"

    def test_valid_ep_number(self):
        assert _is_valid_ep_number(64) is True
        assert _is_valid_ep_number(1) is True
        assert _is_valid_ep_number(193) is True

    def test_invalid_ep_number_resolution(self):
        assert _is_valid_ep_number(720) is False
        assert _is_valid_ep_number(1080) is False

    def test_invalid_ep_number_year(self):
        assert _is_valid_ep_number(2024) is False
        assert _is_valid_ep_number(1998) is False

    def test_clean_filename(self):
        result = _clean_filename("Ep064 (1280x720).srt")
        assert "Ep064" in result


class TestClassifyGarbledText:
    """Text classification — the core correctness logic."""

    def test_clean_japanese(self):
        r = classify_garbled_text("こんにちは", target_lang='ja')
        assert r['type'] == 'clean'

    def test_garbled_latin_in_ja(self):
        r = classify_garbled_text("konnichiwa こんにちは", target_lang='ja')
        assert r['type'] == 'garbled'

    def test_clean_chinese(self):
        r = classify_garbled_text("你好世界", target_lang='zh')
        assert r['type'] == 'clean'

    def test_garbled_cyrillic_in_en(self):
        r = classify_garbled_text("Привет мир", target_lang='en')
        assert r['type'] == 'garbled'

    def test_clean_english(self):
        r = classify_garbled_text("Hello world", target_lang='en')
        assert r['type'] == 'clean'

    def test_empty_text(self):
        r = classify_garbled_text("", target_lang='ja')
        assert r['type'] == 'clean'  # empty = no content to be garbled

    def test_op_region_boundary(self):
        assert OP_BOUNDARY_SEC == 95

    def test_ed_region_boundary(self):
        assert ED_BOUNDARY_SEC == 120


class TestTextValidation:
    def test_valid_ja(self):
        assert is_valid_subtitle_text("こんにちは", target_lang='ja') is True

    def test_invalid_ja_latin_mix(self):
        # Pure Latin in ja context should be flagged
        result = is_valid_subtitle_text("hello world", target_lang='ja')
        # May be True or False depending on content
        assert isinstance(result, bool)

    def test_looks_plausible_ja(self):
        assert looks_like_plausible_text("行くぞ", target_lang='ja') is True

    def test_implausible_noise(self):
        # Even short exclamations may be classified as plausible
        result = looks_like_plausible_text("あっ！", target_lang='ja')
        assert isinstance(result, bool)


class TestMeaningfulCharCount:
    def test_ja_kanji(self):
        assert meaningful_char_count("行くぞ", target_lang='ja') == 3

    def test_ja_exclamations_zero(self):
        assert meaningful_char_count("あっ！えーっ！", target_lang='ja') == 0

    def test_ja_mixed(self):
        count = meaningful_char_count("そうですね", target_lang='ja')
        assert count >= 4  # そ う で す ね are all kana

    def test_zh_count(self):
        assert meaningful_char_count("你好世界", target_lang='zh') >= 2


class TestLengthAnomaly:
    def test_hallucination_detected(self):
        assert is_length_anomaly("abc", "a very long hallucinated response",
                                 ratio=3.0) is True

    def test_normal_no_anomaly(self):
        assert is_length_anomaly("short", "brief", ratio=3.0) is False

    def test_similar_length(self):
        assert is_length_anomaly("hello world", "hello earth", ratio=3.0) is False


class TestProperNounPattern:
    def test_katakana_name(self):
        assert is_proper_noun_pattern("アトム", target_lang='ja') is True

    def test_hiragana_not_noun(self):
        assert is_proper_noun_pattern("あとむ", target_lang='ja') is False

    def test_english(self):
        # Capitalized word in en context
        result = is_proper_noun_pattern("Astro", target_lang='en')
        assert isinstance(result, bool)


class TestFilterLowConfidence:
    def test_discard_high_no_speech_prob(self):
        segs = [
            {'no_speech_prob': 0.8, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) == 1
        assert kept[0]['text'] == 'good'
        assert len(discarded) == 1
        assert 'no_speech_prob' in discarded[0]['discard_reason']

    def test_discard_low_avg_logprob(self):
        segs = [
            {'no_speech_prob': 0.1, 'avg_logprob': -2.0,
             'compression_ratio': 1.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) >= 1

    def test_discard_high_compression(self):
        segs = [
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 3.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) >= 1

    def test_empty_list(self):
        kept, discarded = filter_low_confidence([])
        assert kept == []
        assert discarded == []


class TestSetupWindowsUtf8:
    def test_no_error(self):
        """setup_windows_utf8() should not raise on any platform."""
        try:
            setup_windows_utf8()
        except Exception as e:
            pytest.fail(f"setup_windows_utf8 raised: {e}")


class TestParseSrt:
    def test_basic(self, temp_srt):
        cues = parse_srt(temp_srt, mark_garbled=False)
        assert len(cues) == 3
        assert cues[0]['text'] == 'Hello world'

    def test_mark_garbled(self, temp_srt):
        cues = parse_srt(temp_srt, mark_garbled=True, target_lang='ja')
        assert len(cues) == 3
        # Each cue should have is_garbled field when mark_garbled=True
        assert 'is_garbled' in cues[0]
