"""Project-level multi-source consistency (EN route + JA route share one name set).

Skipped unless ``SABU_GLOSSARY_DIR`` points at the project's glossaries dir,
because these assertions only make sense against the real corpus.
"""

import json
import os
import re

import pytest

from lib.glossary import build_variant_map, load_glossary, load_meta

LATIN = re.compile(r"[A-Za-z][A-Za-z'’.\-]{1,}")
CREDIT = ('字幕制作', 'Society', 'Skaro', 'Hokutono', '翻译', '时间轴')


def _paths():
    gloss_dir = os.environ.get('SABU_GLOSSARY_DIR')
    if not gloss_dir or not os.path.isdir(gloss_dir):
        pytest.skip('SABU_GLOSSARY_DIR not set')
    project = os.path.dirname(os.path.abspath(gloss_dir))
    zh = os.path.join(project, 'translated', 'zh')
    if not os.path.isdir(zh):
        pytest.skip('translated/zh missing')
    return os.path.join(gloss_dir, 'sabu.series.json'), \
        os.path.join(gloss_dir, 'sabu.series.meta.json'), zh


def _texts(zh):
    from lib.subtitle_io import read_subtitles
    out = []
    for name in sorted(os.listdir(zh)):
        if name.endswith('.srt'):
            for cue in read_subtitles(os.path.join(zh, name), mark_garbled=False):
                out.append(str(cue.get('text') or ''))
    return out


def test_no_latin_residue_in_either_route():
    _, _, zh = _paths()
    leftovers = []
    for text in _texts(zh):
        if any(marker in text for marker in CREDIT):
            continue
        tokens = LATIN.findall(text)
        if tokens:
            leftovers.append((tokens, text[:40]))
    assert not leftovers, leftovers[:5]


def test_no_alias_variants_left():
    glossary_path, meta_path, zh = _paths()
    variants = build_variant_map(load_glossary(glossary_path),
                                 load_meta(meta_path))
    leftovers = {}
    for canonical, forms in variants.items():
        for form in forms:
            pattern = (re.compile(r'(?<![A-Za-z])' + re.escape(form) +
                                  r'(?![A-Za-z])', re.IGNORECASE)
                       if LATIN.search(form) else None)
            for text in _texts(zh):
                hit = pattern.search(text) if pattern else (form in text)
                if hit:
                    leftovers.setdefault(form, []).append(text[:30])
                    break
    allowed = {'萨布'}                 # blocked phrase 萨布托米多, intentionally kept
    leftovers = {k: v for k, v in leftovers.items() if k not in allowed}
    assert not leftovers, leftovers
