#!/usr/bin/env python3
"""Source-driven style spec: a handful of keywords, not a story bible.

Why source-driven
-----------------
Anime routinely has backgrounds that say nothing about how characters talk
(think Gintama: Edo setting, modern gags).  A style hint derived from the
synopsis would be confidently wrong.  The transcript, however, *is* the
evidence: sentence endings, archaic forms, honorifics, roughness and loanwords
are measurable, and they are what the translation must follow.

The output is deliberately tiny — ``keywords`` (3–6 short tags) plus an
80-character ``hint`` the translator gets as **reference only**.  The hint must
never override the scene: a character can switch register inside one episode,
and that is normal.

Used by ``nouns/style_spec.py`` (builds the JSON) and
``translate_srt.py --style`` (injects the hint).
"""

from __future__ import annotations

import re

MAX_HINT_CHARS = 80

ARCHAIC = ('ござる', 'でござる', 'にございます', '候', '拙者', '某', 'お主',
           'そなた', '〜のう', 'じゃな', 'であります')
ROUGH = ('やがる', 'てめえ', 'おめえ', '貴様', '野郎', 'くそ', 'ちくしょう',
         'ねえ', 'すげえ', 'ぶっ殺', 'こら')
POLITE = ('です', 'ます', 'ございます', 'ください', 'でしょうか')
HONORIFIC = ('さん', '様', '殿', 'ちゃん', '先生', 'お嬢')
MODERN_LOAN = ('コンピューター', 'テレビ', 'ロボット', 'ビデオ', 'カメラ',
               'スマホ', 'インターネット', 'ゲーム')
KATA = re.compile(r'[\u30a1-\u30fa\u30fc]{2,}')


def _count(text: str, needles) -> int:
    return sum(text.count(n) for n in needles)


def analyze_source_style(ja_cues: list) -> dict:
    """Deterministic register features from the Japanese transcript."""
    texts = [str(c.get('text') or '') for c in ja_cues if str(c.get('text') or '').strip()]
    blob = '\n'.join(texts)
    lines = len(texts) or 1
    chars = len(blob) or 1
    kata = KATA.findall(blob)

    features = {
        'lines': len(texts),
        'chars': chars,
        'median_chars': sorted(len(t) for t in texts)[len(texts) // 2] if texts else 0,
        'archaic': _count(blob, ARCHAIC),
        'rough': _count(blob, ROUGH),
        'polite': _count(blob, POLITE),
        'honorific': _count(blob, HONORIFIC),
        'modern_loan': _count(blob, MODERN_LOAN),
        'katakana_tokens': len(kata),
        'questions': blob.count('？') + blob.count('?'),
        'exclamations': blob.count('！') + blob.count('!'),
    }
    features['archaic_per_100'] = round(100.0 * features['archaic'] / lines, 2)
    features['rough_per_100'] = round(100.0 * features['rough'] / lines, 2)
    features['polite_ratio'] = round(features['polite'] / max(1, features['polite'] + features['rough']), 3)
    features['katakana_ratio'] = round(sum(len(k) for k in kata) / chars, 3)
    features['honorific_per_100'] = round(100.0 * features['honorific'] / lines, 2)
    features['question_ratio'] = round(features['questions'] / lines, 3)
    return features


def derive_keywords(features: dict) -> list[str]:
    """Compress features into 3–6 short, translation-facing tags."""
    tags = []
    if features.get('archaic_per_100', 0) >= 2:
        tags.append('古语腔（でござる/拙者）')
    if features.get('rough_per_100', 0) >= 1:
        tags.append('粗口（やがる/てめえ）')
    if features.get('polite_ratio', 0) >= 0.6:
        tags.append('敬语较多')
    elif features.get('polite_ratio', 0) <= 0.15:
        tags.append('简体口语为主')
    if features.get('honorific_per_100', 0) >= 2:
        tags.append('称谓多（さん/様→先生/大人）')
    if features.get('katakana_ratio', 0) >= 0.12:
        tags.append('片假名多（注意外来语）')
    if features.get('median_chars', 0) <= 14:
        tags.append('短句为主')
    if features.get('question_ratio', 0) >= 0.15:
        tags.append('疑问/反问多')
    if not tags:
        tags.append('现代口语')
    return tags[:6]


def compose_hint(keywords: list, *, max_chars: int = MAX_HINT_CHARS) -> str:
    """One-line, reference-only hint for the translator."""
    body = '、'.join(keywords)
    prefix = '译制风格（参考，勿覆盖语境）：'
    hint = prefix + body
    if len(hint) <= max_chars:
        return hint
    # keep the prefix, trim tags
    kept = []
    for tag in keywords:
        candidate = prefix + '、'.join(kept + [tag])
        if len(candidate) > max_chars:
            break
        kept.append(tag)
    return prefix + '、'.join(kept) if kept else prefix.rstrip('：')


def build_style_spec(ja_cues: list) -> dict:
    features = analyze_source_style(ja_cues)
    keywords = derive_keywords(features)
    return {'schema_version': 1, 'features': features, 'keywords': keywords,
            'hint': compose_hint(keywords)}
