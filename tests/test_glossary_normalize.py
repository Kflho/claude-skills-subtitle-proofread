"""Glossary normalization: reviewed variant table, longest match, no overreach."""

import json

import pytest

from lib.glossary import (
    Normalizer, build_variant_map, discover_variants, explain_variant_map,
    load_glossary, load_meta, normalize_cues,
)

GLOSSARY = {
    'サブ': '佐武', 'サブトイチ': '佐武与市', 'イチ': '市',
    'ヤクザ': '黑道', 'ヌスット': '盗贼', 'バカ': '笨蛋',
}
META = {
    'aliases': {
        '佐武': ['サブヤン', '沙布', '萨布', 'サブ'],
        '佐武与市': ['沙布托伊奇', '萨布托一'],
        '市': ['伊一', '伊奇', '阿市'],
        '黑道': ['黒道'],
        '盗贼': ['盗賊'],
    }
}


def test_build_variant_map_drops_unsafe_entries():
    meta = {'aliases': {
        '佐武': ['佐武', 'サ', 'サブヤン'],
        '市': ['市之', '伊奇'],
        '佐武与市': ['沙布'],
    }}
    kept = build_variant_map(GLOSSARY, meta)

    # no-op, single char, and substring-of-canonical entries are dropped
    assert kept['佐武'] == ['サブヤン']
    # 市之 is a substring of no canonical, kept; 伊奇 kept
    assert set(kept['市']) == {'市之', '伊奇'}
    # 沙布 is a substring of the canonical 佐武与市? no — but it is a substring
    # of another canonical (佐武与市 contains 佐武 not 沙布), so it is kept
    assert '沙布' in kept['佐武与市']


def test_explain_reports_every_drop_reason():
    meta = {'aliases': {'佐武': ['佐武', 'サ', 'サブヤン']}}
    explained = explain_variant_map(GLOSSARY, meta)

    assert explained['kept']['佐武'] == ['サブヤン']
    reasons = {(d['variant'], d['reason']) for d in explained['dropped']}
    assert ('佐武', 'equals canonical') in reasons
    assert ('サ', 'shorter than 2 chars') in reasons


def test_substring_of_canonical_is_dropped():
    meta = {'aliases': {'佐武与市': ['佐武与']}}
    kept = build_variant_map(GLOSSARY, meta)
    assert '佐武与市' not in kept


def test_normalizer_prefers_longest_match():
    normalizer = Normalizer(build_variant_map(GLOSSARY, META))

    text = 'サブヤンが言った。沙布托伊奇も来た。'
    out, hits = normalizer.normalize(text)

    assert out == '佐武が言った。佐武与市も来た。'
    assert [h['variant'] for h in hits] == ['サブヤン', '沙布托伊奇']


def test_normalizer_never_touches_canonical_only_text():
    normalizer = Normalizer(build_variant_map(GLOSSARY, META))
    text = '佐武与市在雾月夜下行走，黑道和盗贼都怕他。'
    out, hits = normalizer.normalize(text)
    assert out == text and hits == []


def test_normalizer_leaves_decoy_words_alone():
    normalizer = Normalizer(build_variant_map(GLOSSARY, META))
    for text in ('市场上人很多', '小偷摸走了钱包', '强盗在山里出没', '这是笨蛋行为'):
        out, hits = normalizer.normalize(text)
        assert out == text and hits == [], text


def test_normalizer_is_noop_without_rules():
    normalizer = Normalizer({})
    assert normalizer.has_rules is False
    assert normalizer.normalize('任何文本') == ('任何文本', [])


def test_normalize_cues_does_not_mutate_input():
    normalizer = Normalizer(build_variant_map(GLOSSARY, META))
    cues = [
        {'start': '00:00:01,000', 'end': '00:00:02,000', 'text': 'サブが来た'},
        {'start': '00:00:03,000', 'end': '00:00:04,000', 'text': '佐武が来た'},
    ]
    new_cues, changes = normalize_cues(cues, normalizer)

    assert cues[0]['text'] == 'サブが来た'          # untouched input
    assert new_cues[0]['text'] == '佐武が来た'
    assert len(changes) == 1
    assert changes[0]['before'] == 'サブが来た'
    assert changes[0]['hits'][0]['canonical'] == '佐武'


def test_load_glossary_skips_metadata_keys(tmp_path):
    path = tmp_path / 'g.json'
    path.write_text(json.dumps({'_comment': 'x', '_meta': {'a': 1},
                                'サブ': '佐武'}, ensure_ascii=False),
                    encoding='utf-8')
    assert load_glossary(str(path)) == {'サブ': '佐武'}


def test_load_meta_handles_missing_file(tmp_path):
    assert load_meta(str(tmp_path / 'nope.json')) == {}


def test_discover_variants_finds_repeated_wrong_forms():
    ja = {'EP001': [
        {'text': 'サブが来た'},
        {'text': 'サブは強い'},
        {'text': 'イチも来た'},
    ]}
    zh = {'EP001': [
        {'text': '沙布来了'},
        {'text': '沙布很强'},
        {'text': '伊一也来了'},
    ]}
    candidates = discover_variants(ja, zh, GLOSSARY, min_count=2)
    names = {c['candidate']: c['positive'] for c in candidates}

    assert names.get('沙布') == 2      # appears where サブ was, canonical missing
    assert '伊一' not in names         # only once → below min_count
    assert all(c['canonical'] == '佐武' for c in candidates
               if c['candidate'] == '沙布')


def test_discover_variants_ignores_cues_that_already_match():
    ja = {'EP001': [{'text': 'サブが来た'}, {'text': 'サブが来た'}]}
    zh = {'EP001': [{'text': '佐武来了'}, {'text': '佐武来了'}]}
    assert discover_variants(ja, zh, GLOSSARY, min_count=2) == []


def test_project_glossary_files_are_consistent():
    """The shipped 佐武 glossary/meta must stay loadable and safe."""
    import os
    root = (r'D:\data\local\software\recreation\video\projects'
            r'\佐武与市捕物控\glossaries')
    glossary_path = os.path.join(root, 'sabu.series.json')
    meta_path = os.path.join(root, 'sabu.series.meta.json')
    if not (os.path.isfile(glossary_path) and os.path.isfile(meta_path)):
        pytest.skip('佐武 glossary not present')

    glossary = load_glossary(glossary_path)
    meta = load_meta(meta_path)
    variant_map = build_variant_map(glossary, meta)

    assert glossary['サブ'] == '佐武'
    assert glossary['サブトイチ'] == '佐武与市'
    # every canonical with aliases must exist in the glossary
    for canonical in variant_map:
        assert canonical in glossary.values(), canonical
    # unsafe aliases must not have been kept
    flat = {v for vs in variant_map.values() for v in vs}
    assert '佐武' not in flat and '市' not in flat
    assert 'サブヤン' in flat


def test_project_aliases_do_not_overreach():
    """Regression: 「圣诞老人」 must stay Santa, not become 「三太老人」.

    The lesson from the first real run: an alias that is a legitimate phrase
    fragment corrupts unrelated lines.  These three were moved out of
    ``aliases`` after the measurement; this test keeps them out.
    """
    import os
    root = (r'D:\data\local\software\recreation\video\projects'
            r'\佐武与市捕物控\glossaries')
    glossary_path = os.path.join(root, 'sabu.series.json')
    meta_path = os.path.join(root, 'sabu.series.meta.json')
    if not (os.path.isfile(glossary_path) and os.path.isfile(meta_path)):
        pytest.skip('佐武 glossary not present')

    variant_map = build_variant_map(load_glossary(glossary_path),
                                    load_meta(meta_path))
    kept = {v for vs in variant_map.values() for v in vs}
    assert '圣诞' not in kept
    assert '阿市' not in kept
    assert '世间暗处' not in kept

    normalizer = Normalizer(variant_map)
    for text in ('圣诞老人，你这小鬼！', '喂，阿市！别担心！',
                 '行走于朦胧月夜，世间暗处'):
        out, hits = normalizer.normalize(text)
        assert out == text and hits == [], text
