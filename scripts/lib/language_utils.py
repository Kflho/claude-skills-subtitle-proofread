#!/usr/bin/env python3
"""Language utility dispatcher — returns the appropriate language module.

Usage:
  from lib.language_utils import get_lang_utils
  LU = get_lang_utils('zh')
  # LU.COMMON_WORDS, LU.NON_DIALOGUE_PATTERNS, etc.
"""


def get_lang_utils(target_lang: str):
    """Return the language utility module for the given target language.

    Args:
        target_lang: 'ja' | 'zh' | 'en'

    Returns:
        Module with language-specific constants:
          - COMMON_WORDS / COMMON_KATAKANA / COMMON_KANJI
          - NON_DIALOGUE_PATTERNS
          - NON_WORD_RE
          - EXCLAMATION_* (chars or words)
          - HONORIFIC_PATTERNS / _HONORIFIC_LIST
    """
    if target_lang == 'zh':
        from lib import chinese_utils as lu
    elif target_lang == 'en':
        from lib import english_utils as lu
    else:
        from lib import japanese_utils as lu
    return lu
