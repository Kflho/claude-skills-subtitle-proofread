"""Tests for language utility modules — constants and imports."""

import pytest
from lib.language_utils import get_lang_utils


class TestGetLangUtils:
    """Module dispatch by language code."""

    def test_ja_returns_japanese_utils(self):
        m = get_lang_utils('ja')
        assert m is not None
        assert hasattr(m, 'COMMON_KATAKANA')
        assert hasattr(m, 'COMMON_KANJI')

    def test_zh_returns_chinese_utils(self):
        m = get_lang_utils('zh')
        assert m is not None
        assert hasattr(m, 'COMMON_WORDS')
        assert hasattr(m, 'TRAD_TO_SIMP_MAP')

    def test_en_returns_english_utils(self):
        m = get_lang_utils('en')
        assert m is not None
        assert hasattr(m, 'COMMON_WORDS')

    def test_unknown_lang_returns_fallback(self):
        m = get_lang_utils('fr')
        assert m is not None
        assert hasattr(m, 'COMMON_WORDS') or hasattr(m, 'COMMON_KATAKANA')


class TestChineseUtils:
    """Tests for lib/chinese_utils.py constants."""

    def test_common_words_has_expected(self):
        from lib.chinese_utils import COMMON_WORDS
        assert len(COMMON_WORDS) > 100  # substantial word list
        assert '的' in COMMON_WORDS
        assert '我' in COMMON_WORDS

    def test_trad_to_simp_map(self):
        from lib.chinese_utils import TRAD_TO_SIMP_MAP
        assert len(TRAD_TO_SIMP_MAP) > 0
        # The map uses Unicode code points as keys (optimized for str.translate)
        assert isinstance(TRAD_TO_SIMP_MAP, dict)

    def test_pinyin_tones(self):
        from lib.chinese_utils import PINYIN_TONES
        assert isinstance(PINYIN_TONES, dict)
        assert 'ā'.translate(PINYIN_TONES) == 'a'


class TestJapaneseUtils:
    """Tests for lib/japanese_utils.py constants."""

    def test_common_katakana_has_expected(self):
        from lib.japanese_utils import COMMON_KATAKANA
        assert len(COMMON_KATAKANA) > 100

    def test_common_kanji_has_expected(self):
        from lib.japanese_utils import COMMON_KANJI
        assert len(COMMON_KANJI) > 100

    def test_non_word_re(self):
        from lib.japanese_utils import NON_WORD_RE
        assert NON_WORD_RE.match('ーーー') is not None
        assert NON_WORD_RE.match('ふふふ') is not None
        assert NON_WORD_RE.match('こんにちは') is None


class TestEnglishUtils:
    """Tests for lib/english_utils.py constants."""

    def test_common_words_has_expected(self):
        from lib.english_utils import COMMON_WORDS
        assert len(COMMON_WORDS) > 100
        assert 'the' in COMMON_WORDS

    def test_exclamation_words(self):
        from lib.english_utils import EXCLAMATION_WORDS
        assert len(EXCLAMATION_WORDS) > 5
        assert 'um' in EXCLAMATION_WORDS
