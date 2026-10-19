"""Reading-level ASR name repair: fixes homophones, leaves ordinary words."""

import pytest

from lib.asr.namefix import (
    NameEntry, fix_cues, fix_text, load_name_entries,
)

GLOSSARY = {'サブ': '佐武', '市': '市', 'サブトイチ': '佐武与市',
            'イチ': '市', 'イチアン': '一庵', 'バカ': '笨蛋'}
META = {
    'readings': {'サブ': ['サブ'], '市': ['イチ'], 'イチ': ['イチ'],
                 'サブトイチ': ['サブトイチ'], 'イチアン': ['イチアン'],
                 'バカ': ['バカ']},
    'canonical_ja': {'サブ': '佐武', 'イチ': '市', 'サブトイチ': '佐武と市',
                     'イチアン': '一庵'},
}


@pytest.fixture(scope='module')
def tokenizer():
    from janome.tokenizer import Tokenizer
    return Tokenizer()


@pytest.fixture(scope='module')
def entries():
    return load_name_entries(GLOSSARY, META)


def test_readings_come_from_meta_not_janome(entries):
    """janome reads 佐武 as サタケ and 市 as シ — the table must win."""
    readings = {r for e in entries for r in e.readings}
    assert 'サブ' in readings and 'イチ' in readings
    surfaces = {e.canonical_ja for e in entries}
    assert '佐武' in surfaces and '佐武と市' in surfaces


def test_homophone_window_is_repaired(entries, tokenizer):
    text = '闇を切り裂く男行きおぼろずき世のサブト位置'
    out, hits = fix_text(text, entries, tokenizer=tokenizer)

    assert out.endswith('世の佐武と市')
    assert len(hits) == 1
    assert hits[0]['from'] == 'サブト位置'
    assert hits[0]['reading'] == 'サブトイチ'
    assert hits[0]['distance'] == 0


def test_bare_position_kanji_is_not_rewritten(entries, tokenizer):
    text = '行く位置のないことを言いなさい'
    out, hits = fix_text(text, entries, tokenizer=tokenizer)
    assert out == text and hits == []


def test_plain_names_are_normalized(entries, tokenizer):
    out, hits = fix_text('冷たいサブヤンだね', load_name_entries(
        {'サブヤン': '佐武'}, {'readings': {'サブヤン': ['サブヤン']},
                             'canonical_ja': {'サブヤン': '佐武'}}),
        tokenizer=tokenizer)
    assert out == '冷たい佐武だね'


def test_katakana_word_is_not_split(entries, tokenizer):
    text = 'サブタイトルを表示する'
    out, hits = fix_text(text, entries, tokenizer=tokenizer)
    assert out == text and hits == []


def test_honorific_suffix_is_preserved(entries, tokenizer):
    """サブ君 → 佐武君: the name is replaced, 君 is not swallowed."""
    out, hits = fix_text('サブ君が来た', entries, tokenizer=tokenizer)
    assert out == '佐武君が来た'
    assert hits[0]['from'] in ('サブ', 'サブ君')


def test_near_miss_off_by_default_but_available(entries, tokenizer):
    """One-mora substitutions damaged real words (バカ野→バカ); opt-in only."""
    text = 'バカ野たら'
    out, hits = fix_text(text, entries, tokenizer=tokenizer)
    assert out == text and hits == []

    out, hits = fix_text(text, entries, tokenizer=tokenizer, near_miss=True)
    assert out != text      # documented risk, not the default


def test_canonical_text_is_left_alone(entries, tokenizer):
    text = '佐武と市が来た'
    out, hits = fix_text(text, entries, tokenizer=tokenizer)
    assert out == text and hits == []


def test_fix_cues_does_not_mutate_input(entries, tokenizer):
    cues = [{'start': '00:00:01,000', 'end': '00:00:02,000',
             'text': 'サブとイチが来た'},
            {'start': '00:00:03,000', 'end': '00:00:04,000',
             'text': '佐武と市が来た'}]
    new_cues, changes = fix_cues(cues, entries, tokenizer=tokenizer)

    assert cues[0]['text'] == 'サブとイチが来た'
    assert new_cues[0]['text'].startswith('佐武と市')
    assert len(changes) == 1
    assert changes[0]['hits'][0]['to'] in ('佐武', '市', '佐武と市')


def test_kanji_without_reading_is_skipped():
    entries = load_name_entries({'佐武': '佐武'}, {})
    assert entries == []


def test_empty_text_is_safe(entries):
    assert fix_text('', entries) == ('', [])


def test_allow_surfaces_can_whitelist_a_kanji_homophone(entries, tokenizer):
    out, hits = fix_text('位置に行く', entries, tokenizer=tokenizer,
                         allow_surfaces=['位置'])
    assert out == '市に行く'
    assert hits[0]['from'] == '位置'
