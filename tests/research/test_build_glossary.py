"""Tests for nouns/build_glossary.py — variant grouping, filtering, markdown."""

import json
import sys
import types

from nouns import build_glossary as bg


def _glossary(**overrides):
    base = {'characters': [], 'places': [], 'organizations': [],
            'other_terms': [], 'kanji_compounds': []}
    base.update(overrides)
    return base


def _install_fake_jamdict(monkeypatch, known_words=()):
    """Fake jamdict so build_glossary never loads the real dictionary."""
    class _Result:
        def __init__(self, n):
            self.entries = [object()] * n

    class _Jamdict:
        def lookup(self, word):
            return _Result(1 if word in known_words else 0)

    mod = types.ModuleType('jamdict')
    mod.Jamdict = _Jamdict
    monkeypatch.setitem(sys.modules, 'jamdict', mod)


# ═══════════════════════════════════════════════════════════════
# Variant grouping
# ═══════════════════════════════════════════════════════════════

def test_norm_for_grouping_strips_long_vowels_and_small_kana():
    assert bg._norm_for_grouping('スーパー') == 'スパ'
    assert bg._norm_for_grouping('キャラ') == 'キヤラ'
    assert bg._norm_for_grouping('アトム') == 'アトム'


def test_group_katakana_variants_picks_most_frequent_canonical():
    groups = bg.group_katakana_variants([
        ('スーパー', 5), ('スパー', 2), ('アトム', 9), ('サ', 7),
    ])

    assert [g['canonical'] for g in groups] == ['アトム', 'スーパー']
    super_group = groups[1]
    assert super_group['variants'] == [
        {'word': 'スーパー', 'freq': 5}, {'word': 'スパー', 'freq': 2}]
    assert super_group['total_freq'] == 7


def test_group_katakana_variants_empty():
    assert bg.group_katakana_variants([]) == []


# ═══════════════════════════════════════════════════════════════
# build_glossary — filtering and classification
# ═══════════════════════════════════════════════════════════════

def test_build_glossary_filters_noise_and_common_words():
    terms = {
        'サリー': 10,       # katakana, short, frequent → character
        'アトム': 4,        # katakana, short, rare → other term
        'テレビ': 9,        # COMMON_KATAKANA → dropped
        '今日': 8,          # COMMON_KANJI → dropped
        '音楽': 7,          # OP/ED bracket label → dropped
        'あああ': 5,        # NON_WORD pattern → dropped
        '鉄腕': 3,          # kanji compound → kept
        'サ': 9,            # too short katakana → dropped
        'ア': 5,            # too short katakana → dropped
    }

    glossary = bg.build_glossary(terms, min_freq=3, lang='ja',
                                 use_jamdict=False)

    assert [g['canonical'] for g in glossary['characters']] == ['サリー']
    assert [g['canonical'] for g in glossary['other_terms']] == ['アトム']
    assert [k['word'] for k in glossary['kanji_compounds']] == ['鉄腕']


def test_build_glossary_respects_min_freq():
    glossary = bg.build_glossary({'サリー': 2, '鉄腕': 1}, min_freq=3,
                                 lang='ja', use_jamdict=False)
    assert glossary == _glossary()


def test_build_glossary_honorific_suffix_marks_character():
    glossary = bg.build_glossary(
        {'ウランちゃん': 6, 'タイムマシン': 6}, min_freq=3, lang='ja',
        use_jamdict=False)

    assert [g['canonical'] for g in glossary['characters']] == ['ウランちゃん']
    assert [g['canonical'] for g in glossary['other_terms']] == ['タイムマシン']


def test_build_glossary_zh_mode_ignores_katakana():
    glossary = bg.build_glossary({'阿童木': 5, 'サリー': 5}, min_freq=3,
                                 lang='zh', use_jamdict=False)

    assert glossary['characters'] == []
    assert [k['word'] for k in glossary['kanji_compounds']] == ['阿童木']


def test_build_glossary_jamdict_filter(monkeypatch):
    _install_fake_jamdict(monkeypatch, known_words={'アトム'})

    glossary = bg.build_glossary({'アトム': 9, 'サリー': 9}, min_freq=3,
                                 lang='ja', use_jamdict=True)

    assert [g['canonical'] for g in glossary['characters']] == ['サリー']


# ═══════════════════════════════════════════════════════════════
# AI-enriched nouns
# ═══════════════════════════════════════════════════════════════

def test_merge_ai_nouns_adds_entries_without_duplicates(tmp_path):
    ai = tmp_path / 'ai_nouns.json'
    ai.write_text(json.dumps({
        'characters': ['アトム', 'サリー', 'サリー'],
        'places': ['御茶ノ水'],
        'organizations': ['科学省'],
        'terms': ['タイムマシン'],
    }, ensure_ascii=False), encoding='utf-8')
    glossary = _glossary(
        characters=[{'canonical': 'アトム', 'variants': [
            {'word': 'アトム', 'freq': 5}, {'word': 'アットム', 'freq': 1}],
            'total_freq': 6}],
        kanji_compounds=[{'word': '御茶ノ水', 'freq': 2}],
    )

    out = bg._merge_ai_nouns(glossary, str(ai))

    names = [g['canonical'] for g in out['characters']]
    assert names == ['アトム', 'サリー']
    assert out['characters'][1]['source'] == '[AI]'
    assert out['characters'][1]['total_freq'] == 0
    # 御茶ノ水 already in kanji table → not duplicated
    assert [k['word'] for k in out['kanji_compounds']] == ['御茶ノ水', '科学省']
    assert out['other_terms'][0]['canonical'] == 'タイムマシン'


def test_merge_ai_nouns_bad_file_is_noop(tmp_path, capsys):
    bad = tmp_path / 'ai.json'
    bad.write_text('not json', encoding='utf-8')
    glossary = _glossary()

    assert bg._merge_ai_nouns(glossary, str(bad)) == glossary
    assert 'Failed to read AI nouns' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Parsing existing markdown (preserve manual/AI entries)
# ═══════════════════════════════════════════════════════════════

EXISTING_MD = """## 角色名（自动提取）

| 日语 | 出现次数 | 拼写变体 |
|------|---------|---------|
| アトム[AI] | 0 | — |
| サリー | 12 | — |
| ウラン | 0 | ウランちゃん |

## 汉字复合词

| 日语 | 出现次数 |
|------|---------|
| 御茶ノ水[manual] | 0 |
| 鉄腕 | 9 |

## 其他片假名术语

| 日语 | 出现次数 | 拼写变体 |
|------|---------|---------|
| タイムマシン | 0 | — |
"""


def test_parse_existing_glossary_keeps_only_manual_and_ai(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text(EXISTING_MD, encoding='utf-8')

    preserved = bg._parse_existing_glossary(str(path))

    assert [c['canonical'] for c in preserved['characters']] == [
        'アトム', 'ウラン']
    assert preserved['characters'][0]['source'] == '[AI]'
    # freq>0 without source tag → regenerated, not preserved
    assert [k['word'] for k in preserved['kanji_compounds']] == ['御茶ノ水']
    assert preserved['kanji_compounds'][0]['source'] == '[manual]'
    assert [t['canonical'] for t in preserved['other_terms']] == [
        'タイムマシン']


def test_parse_existing_glossary_missing_file(tmp_path):
    assert bg._parse_existing_glossary(str(tmp_path / 'nope.md')) is None


def test_merge_preserved_does_not_overwrite_existing():
    glossary = _glossary(
        characters=[{'canonical': 'アトム', 'variants': [], 'total_freq': 9}],
        kanji_compounds=[{'word': '鉄腕', 'freq': 3}],
    )
    preserved = {
        'characters': [
            {'canonical': 'アトム', 'variants': [], 'total_freq': 0},
            {'canonical': 'サリー', 'variants': [], 'total_freq': 0},
        ],
        'kanji_compounds': [{'word': '鉄腕', 'freq': 0},
                            {'word': '御茶ノ水', 'freq': 0}],
        'other_terms': [{'canonical': 'タイムマシン', 'variants': [],
                         'total_freq': 0}],
    }

    out = bg._merge_preserved(glossary, preserved)

    assert [c['canonical'] for c in out['characters']] == ['アトム', 'サリー']
    assert out['characters'][0]['total_freq'] == 9      # not overwritten
    assert [k['word'] for k in out['kanji_compounds']] == ['鉄腕', '御茶ノ水']
    assert out['other_terms'][0]['canonical'] == 'タイムマシン'


# ═══════════════════════════════════════════════════════════════
# Markdown rendering
# ═══════════════════════════════════════════════════════════════

def test_format_glossary_markdown_tables():
    glossary = _glossary(
        characters=[{'canonical': 'アトム',
                     'variants': [{'word': 'アトム', 'freq': 3},
                                  {'word': 'アットム', 'freq': 1}],
                     'total_freq': 4}],
        kanji_compounds=[{'word': '鉄腕', 'freq': 5}],
        other_terms=[{'canonical': 'テレビ',
                      'variants': [{'word': 'テレビ', 'freq': 2}],
                      'total_freq': 2, 'source': '[AI]'}],
    )

    md = bg.format_glossary_markdown(glossary)

    assert '## 角色名（自动提取）' in md
    assert '| アトム | 4 | アットム |' in md
    assert '## 汉字复合词' in md
    assert '| 鉄腕 | 5 |' in md
    assert '## 其他片假名术语' in md
    assert '| テレビ[AI] | 2 | — |' in md
    assert '## 使用方法' in md


def test_format_glossary_markdown_truncates_long_tables():
    chars = [{'canonical': f'C{i:02d}', 'variants': [{'word': f'C{i:02d}',
                                                      'freq': 1}],
              'total_freq': 1} for i in range(85)]
    others = [{'canonical': f'O{i:02d}',
               'variants': [{'word': f'O{i:02d}', 'freq': 1}],
               'total_freq': 1} for i in range(65)]

    md = bg.format_glossary_markdown(_glossary(characters=chars,
                                               other_terms=others))

    assert '| C79 |' in md and '| C80 |' not in md
    assert '| O59 |' in md and '| O60 |' not in md


# ═══════════════════════════════════════════════════════════════
# Machine-readable mappings
# ═══════════════════════════════════════════════════════════════

def test_write_mappings_json_collects_terms_and_preserves_zh(tmp_path):
    glossary = _glossary(
        characters=[{'canonical': 'アトム',
                     'variants': [{'word': 'アトム'}, {'word': 'アットム'},
                                  {'word': 'ア'}]},
                    {'canonical': 'サ', 'variants': []}],
        kanji_compounds=[{'word': '鉄腕'}, {'word': '鉄'}],
        other_terms=[{'canonical': 'タイムマシン', 'variants': []}],
    )
    out = tmp_path / 'noun_mappings.json'
    out.write_text(json.dumps({'アトム': '阿童木', '既存': '旧'},
                              ensure_ascii=False), encoding='utf-8')

    bg._write_mappings_json(glossary, str(out), str(tmp_path / 'md'))

    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['アトム'] == '阿童木'          # AI-filled value preserved
    assert data['アットム'] == ''
    assert data['鉄腕'] == '' and data['タイムマシン'] == ''
    assert 'ア' not in data and 'サ' not in data and '鉄' not in data
    assert '既存' not in data


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _write_findings(path, terms):
    path.write_text(json.dumps({'term_frequencies': terms},
                               ensure_ascii=False), encoding='utf-8')


def test_main_builds_markdown_and_mappings(monkeypatch, tmp_path):
    _install_fake_jamdict(monkeypatch)
    findings = tmp_path / 'findings.json'
    _write_findings(findings, {'サリー': 10, 'アトム': 4, '鉄腕': 4})
    md = tmp_path / 'proper-nouns.md'
    mappings = tmp_path / 'noun_mappings.json'

    monkeypatch.setattr(sys, 'argv', [
        'build_glossary.py', '--findings', str(findings), '--output', str(md),
        '--mappings-output', str(mappings)])
    bg.main()

    text = md.read_text(encoding='utf-8')
    assert 'サリー' in text and 'アトム' in text and '鉄腕' in text
    data = json.loads(mappings.read_text(encoding='utf-8'))
    assert set(data) == {'サリー', 'アトム', '鉄腕'}
    assert all(v == '' for v in data.values())


def test_main_preserves_manual_entries_on_rebuild(monkeypatch, tmp_path):
    _install_fake_jamdict(monkeypatch)
    findings = tmp_path / 'findings.json'
    _write_findings(findings, {'サリー': 10})
    md = tmp_path / 'proper-nouns.md'
    md.write_text(
        '## 角色名（自动提取）\n\n'
        '| 日语 | 出现次数 | 拼写变体 |\n'
        '|------|---------|---------|\n'
        '| ドラ[AI] | 0 | — |\n', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [
        'build_glossary.py', '--findings', str(findings), '--output', str(md)])
    bg.main()

    text = md.read_text(encoding='utf-8')
    assert 'サリー' in text and 'ドラ[AI]' in text


def test_main_no_merge_discards_manual_entries(monkeypatch, tmp_path):
    _install_fake_jamdict(monkeypatch)
    findings = tmp_path / 'findings.json'
    _write_findings(findings, {'サリー': 10})
    md = tmp_path / 'proper-nouns.md'
    md.write_text(
        '## 角色名（自动提取）\n\n'
        '| 日语 | 出现次数 | 拼写变体 |\n'
        '|------|---------|---------|\n'
        '| ドラ[AI] | 0 | — |\n', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [
        'build_glossary.py', '--findings', str(findings), '--output', str(md),
        '--no-merge'])
    bg.main()

    text = md.read_text(encoding='utf-8')
    assert 'サリー' in text and 'ドラ' not in text


def test_main_requires_term_frequencies(monkeypatch, tmp_path):
    findings = tmp_path / 'findings.json'
    findings.write_text('{}', encoding='utf-8')

    monkeypatch.setattr(sys, 'argv', [
        'build_glossary.py', '--findings', str(findings),
        '--output', str(tmp_path / 'md')])

    try:
        bg.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError('expected SystemExit')
