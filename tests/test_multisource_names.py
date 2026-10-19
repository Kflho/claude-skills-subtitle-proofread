"""Multi-source name unification: EN-route and JA-route must land on one name.

The Sabu project translated 30 episodes from English and 22 from Japanese ASR.
English subtitles kept romaji names (Sabu/Midori), the Japanese route kept kana;
both end up in the same Chinese subtitle set, so the same character must have
exactly one Chinese form.  These tests model that with fixtures, so they run
without the real corpus.
"""

import json

import pytest

from eval import text_gate
from lib.glossary import (
    Normalizer, build_normalizer, build_variant_map, load_glossary, load_meta,
)

GLOSSARY = {
    'サブ': '佐武', 'Sabu': '佐武', 'SABU': '佐武',
    'ミドリ': '小绿', 'Midori': '小绿',
    'サブトイチ': '佐武与市',
}
META = {
    'aliases': {
        '佐武': ['サブ', 'Sabu', 'SABU', '萨布'],
        '小绿': ['ミドリ', 'Midori'],
        '佐武与市': ['sabu to ichi'],
    },
}


def _normalizer():
    return build_normalizer(GLOSSARY, META)


def test_two_sources_produce_the_same_chinese_names():
    normalizer = _normalizer()
    en_output = normalizer.normalize('Sabu和Midori来了，SABU笑了')[0]
    ja_output = normalizer.normalize('サブとミドリが来た')[0]

    assert en_output == '佐武和小绿来了，佐武笑了'
    assert ja_output == '佐武と小绿が来た'
    # the canonical names are identical no matter which source produced the line
    for name in ('佐武', '小绿'):
        assert name in en_output and name in ja_output
    assert 'Sabu' not in en_output and 'サブ' not in ja_output



def test_english_alias_is_case_insensitive_but_not_substring():
    normalizer = _normalizer()
    assert normalizer.normalize('midori来了')[0] == '小绿来了'
    assert normalizer.normalize('MIDORI来了')[0] == '小绿来了'
    # no word-boundary match inside a longer romaji word
    assert normalizer.normalize('Midorix')[0] == 'Midorix'


def test_longest_alias_wins_across_sources():
    glossary = dict(GLOSSARY)
    glossary['sabu to ichi'] = '佐武与市'
    meta = {'aliases': dict(META['aliases'])}
    meta['aliases']['佐武与市'] = ['sabu to ichi']
    normalizer = build_normalizer(glossary, meta)
    assert normalizer.normalize('sabu to ichi来了')[0] == '佐武与市来了'
    assert normalizer.normalize('Sabu来了')[0] == '佐武来了'


def test_each_alias_belongs_to_one_canonical():
    variant_map = build_variant_map(GLOSSARY, META)
    seen = {}
    for canonical, variants in variant_map.items():
        for variant in variants:
            assert variant not in seen or seen[variant] == canonical, variant
            seen[variant] = canonical


def test_gate_catches_latin_before_normalization_and_passes_after(tmp_path):
    en = tmp_path / 'en'
    ja = tmp_path / 'ja'
    en.mkdir()
    ja.mkdir()
    (en / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSabu和Midori来了\n\n",
        encoding='utf-8')
    (ja / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nサブとミドリが来た\n\n",
        encoding='utf-8')

    before = text_gate.main(['--zh-dir', str(en), '--max-latin', '0',
                             '--report', str(tmp_path / 'before.json')])
    assert before == 1
    data = json.loads((tmp_path / 'before.json').read_text(encoding='utf-8'))
    assert data['totals']['latin'] == 2
    assert data['totals']['kana'] == 0        # EN route: no kana, only latin

    normalizer = _normalizer()
    for directory in (en, ja):
        path = directory / 'EP001.srt'
        text = path.read_text(encoding='utf-8')
        new_text, _ = normalizer.normalize(text)
        path.write_text(new_text, encoding='utf-8')

    assert text_gate.main(['--zh-dir', str(en), '--max-latin', '0']) == 0
    assert text_gate.main(['--zh-dir', str(ja), '--max-latin', '0']) == 1
