"""Tests for nouns/aggregate.py — mechanical cross-episode merge."""

import json
import sys

from nouns import aggregate as agg


def ent(ja=(), zh=(), count=1, samples=()):
    return {'ja_forms': list(ja), 'zh_forms': list(zh), 'count': count,
            'samples': list(samples)}


def entry(ja=(), variants=(), episodes=(), scope='auto', count=0):
    return {'ja_forms': list(ja), 'zh_variants': list(variants),
            'episodes': list(episodes), 'scope': scope, 'count': count}


# ═══════════════════════════════════════════════════════════════
# Rendering for the LLM prompt
# ═══════════════════════════════════════════════════════════════

def test_render_map_orders_and_labels_scope():
    noun_map = {
        'アトム': entry(ja=['アトム'], variants=['阿童木'],
                       episodes=['EP001', 'EP002']),
        'サリー': entry(ja=['サリー'], episodes=['EP001'],
                       scope='per_episode'),
        '博士': entry(ja=['博士'], episodes=[], scope='global'),
    }

    lines, idx = agg._render_map(noun_map)

    assert lines[0].startswith('1. アトム')
    assert '[2集]' in lines[0]
    assert '日文: アトム' in lines[0]
    assert '中文变体: 阿童木' in lines[0]
    assert any('[单集]' in line for line in lines)
    assert any('[global]' in line for line in lines)
    assert idx == {1: 'アトム', 2: 'サリー', 3: '博士'}


def test_render_map_truncates_to_max_entries():
    noun_map = {f'N{i}': entry(episodes=[f'EP{i:03d}']) for i in range(5)}
    lines, idx = agg._render_map(noun_map, max_entries=2)
    assert len(lines) == 2
    assert list(idx) == [1, 2]


def test_render_map_empty():
    assert agg._render_map({}) == ([], {})
    assert agg._render_map(None) == ([], {})


def test_render_entities_letters_and_sample_truncation():
    ents = [ent(ja=['アトム'], zh=['阿童木'],
                samples=['あ' * 120 + '|中文'])]
    lines = agg._render_entities(ents)

    assert lines[0].startswith('A. ja:[アトム] zh:[阿童木]')
    assert len(lines[0].split('样例:')[1]) == 80


def test_render_entities_falls_back_to_x_index_after_z():
    lines = agg._render_entities([ent(ja=['x'])] * 27)
    assert lines[0].startswith('A.') and lines[25].startswith('Z.')
    assert lines[26].startswith('X26.')


# ═══════════════════════════════════════════════════════════════
# Mechanical merge
# ═══════════════════════════════════════════════════════════════

def test_merge_mechanical_matches_canonical_zh():
    noun_map = {'莎莉': entry(ja=['サリー'], episodes=['EP001'], count=2)}

    _, remaining = agg._merge_mechanical(
        noun_map, 'EP002', [ent(ja=['サリー'], zh=['莎莉'], count=3)])

    assert remaining == []
    assert noun_map['莎莉']['episodes'] == ['EP001', 'EP002']
    assert noun_map['莎莉']['count'] == 5


def test_merge_mechanical_matches_shared_ja_form_and_keeps_zh_variant():
    noun_map = {'阿童木': entry(ja=['アトム'], episodes=['EP001'])}

    agg._merge_mechanical(
        noun_map, 'EP002', [ent(ja=['アトム'], zh=['阿托姆'])])

    assert noun_map['阿童木']['zh_variants'] == ['阿托姆']
    assert noun_map['阿童木']['episodes'] == ['EP001', 'EP002']


def test_merge_mechanical_matches_shared_zh_variant():
    noun_map = {'阿童木': entry(ja=['アトム'], variants=['阿托姆'],
                                episodes=['EP001'])}

    agg._merge_mechanical(noun_map, 'EP002', [ent(ja=['アットム'],
                                                 zh=['阿托姆'])])

    assert 'アットム' in noun_map['阿童木']['ja_forms']


def test_merge_mechanical_leaves_unrelated_entities_remaining():
    noun_map = {'阿童木': entry(ja=['アトム'], episodes=['EP001'])}
    other = ent(ja=['サリー'], zh=['莎莉'])

    _, remaining = agg._merge_mechanical(noun_map, 'EP002', [other])

    assert remaining == [other]
    assert list(noun_map) == ['阿童木']


def test_apply_merge_excludes_canonical_from_variants():
    target = entry(count=1)
    agg._apply_merge(target, ent(ja=['A'], zh=['甲', '乙'], count=2),
                     'EP001', canonical='甲')

    assert target['ja_forms'] == ['A']
    assert target['zh_variants'] == ['乙']
    assert target['episodes'] == ['EP001']
    assert target['count'] == 3


# ═══════════════════════════════════════════════════════════════
# New entries / defaults
# ═══════════════════════════════════════════════════════════════

def test_apply_new_builds_entry_and_strips_canonical_variant():
    noun_map = {}
    agg._apply_new(noun_map, '莎莉',
                   ent(ja=['サリー'], zh=['莎莉', '莎莉酱'], count=4), 'EP001')

    assert noun_map['莎莉'] == {
        'ja_forms': ['サリー'],
        'zh_variants': ['莎莉酱'],
        'episodes': ['EP001'],
        'scope': 'auto',
        'source': 'extract',
        'count': 4,
    }


def test_apply_new_ignores_empty_or_duplicate_canonical():
    noun_map = {'莎莉': entry(ja=['サリー'])}
    agg._apply_new(noun_map, '', ent(zh=['甲']), 'EP001')
    agg._apply_new(noun_map, '   ', ent(zh=['甲']), 'EP001')
    agg._apply_new(noun_map, '莎莉', ent(zh=['甲']), 'EP001')
    assert list(noun_map) == ['莎莉']


def test_apply_new_keeps_explicit_scope():
    noun_map = {}
    agg._apply_new(noun_map, '莎莉', ent(zh=['莎莉']), 'EP001',
                   scope='per_episode')
    assert noun_map['莎莉']['scope'] == 'per_episode'


def test_default_canonical_picks_longest_and_handles_empty():
    assert agg._default_canonical(ent(zh=['阿', '阿童木', '阿托姆'])) == '阿童木'
    assert agg._default_canonical(ent(zh=[])) == '(未命名)'


# ═══════════════════════════════════════════════════════════════
# Applying LLM decisions
# ═══════════════════════════════════════════════════════════════

def test_apply_decisions_merge_new_and_ignore():
    noun_map = {'莎莉': entry(ja=['サリー'], episodes=['EP001'], count=1)}
    ents = [ent(ja=['サリー'], zh=['莎莉酱'], count=2),   # A → merge
            ent(ja=['アトム'], zh=['阿童木'], count=3),   # B → new
            ent(ja=['なに'], zh=['什么'], count=9)]       # C → ignore
    decisions = {
        'A': {'action': 'merge', 'canonical': 1},
        'B': {'action': 'new', 'canonical': '阿童木'},
        'C': {'action': 'ignore'},
    }

    agg._apply_decisions(noun_map, 'EP002', ents, decisions)

    assert noun_map['莎莉']['zh_variants'] == ['莎莉酱']
    assert noun_map['莎莉']['count'] == 3
    assert noun_map['阿童木']['ja_forms'] == ['アトム']
    assert set(noun_map) == {'莎莉', '阿童木'}


def test_apply_decisions_merge_by_canonical_name():
    noun_map = {'莎莉': entry(ja=['サリー'], episodes=['EP001'])}
    ents = [ent(ja=['サリィ'], zh=['莎莉'], count=1)]
    decisions = {'A': {'action': 'merge', 'canonical': '莎莉'}}

    agg._apply_decisions(noun_map, 'EP002', ents, decisions)

    assert 'サリィ' in noun_map['莎莉']['ja_forms']
    assert noun_map['莎莉']['episodes'] == ['EP001', 'EP002']


def test_apply_decisions_without_decision_defaults_to_new():
    noun_map = {}
    ents = [ent(ja=['アトム'], zh=['阿', '阿童木'], count=2)]

    agg._apply_decisions(noun_map, 'EP001', ents, {})

    assert list(noun_map) == ['阿童木']       # longest zh_form
    assert noun_map['阿童木']['ja_forms'] == ['アトム']


# ═══════════════════════════════════════════════════════════════
# merge_into_map
# ═══════════════════════════════════════════════════════════════

def test_merge_into_map_empty_entities_preserves_meta():
    noun_map = {'_meta': {'v': 1}}
    assert agg.merge_into_map(noun_map, 'EP001', []) is noun_map
    assert noun_map['_meta'] == {'v': 1}


def test_merge_into_map_without_overlap_uses_llm(monkeypatch):
    noun_map = {'莎莉': entry(ja=['サリー'], episodes=['EP001'])}

    def fake_llm(noun_map_, episode_id, entities, api_key, model, base_url):
        assert episode_id == 'EP002'
        assert [e['ja_forms'] for e in entities] == [['アトム']]
        return {'A': {'action': 'new', 'canonical': '阿童木'}}

    monkeypatch.setattr(agg, '_decide_with_llm', fake_llm)

    agg.merge_into_map(noun_map, 'EP002',
                       [ent(ja=['アトム'], zh=['阿童木'], count=3)])

    assert noun_map['阿童木']['count'] == 3
    assert set(noun_map) == {'莎莉', '阿童木'}


def test_merge_into_map_llm_failure_keeps_all_entities(monkeypatch):
    monkeypatch.setattr(agg, '_decide_with_llm', lambda *a, **k: None)
    noun_map = {}

    agg.merge_into_map(noun_map, 'EP001',
                       [ent(ja=['アトム'], zh=['阿童木']),
                        ent(ja=['サリー'], zh=['莎莉'])])

    assert set(noun_map) == {'阿童木', '莎莉'}


def test_merge_into_map_normalizes_forms(monkeypatch):
    monkeypatch.setattr(agg, '_decide_with_llm', lambda *a, **k: None)
    noun_map = {}

    agg.merge_into_map(noun_map, 'EP002',
                       [ent(ja=['アトム', 'アトム'], zh=['阿童木', '阿童木'],
                            count=2)])

    assert noun_map['阿童木']['ja_forms'] == ['アトム']
    assert noun_map['阿童木']['zh_variants'] == []
    assert noun_map['阿童木']['episodes'] == ['EP002']


# ═══════════════════════════════════════════════════════════════
# Seed / sidecar helpers
# ═══════════════════════════════════════════════════════════════

def test_seed_from_mapping_groups_by_chinese_name(tmp_path):
    seed = tmp_path / 'noun_mappings.json'
    seed.write_text(json.dumps({'アトム': '阿童木', 'アットム': '阿童木',
                                'サリー': '莎莉', '空': ''},
                               ensure_ascii=False), encoding='utf-8')

    noun_map = agg.seed_from_mapping({}, str(seed))

    assert set(noun_map) == {'阿童木', '莎莉'}
    assert noun_map['阿童木']['ja_forms'] == ['アトム', 'アットム']
    assert noun_map['阿童木']['source'] == 'seed'
    assert noun_map['阿童木']['episodes'] == []


def test_seed_from_mapping_reuses_existing_entry(tmp_path):
    seed = tmp_path / 'noun_mappings.json'
    seed.write_text(json.dumps({'アトム': '阿童木'}, ensure_ascii=False),
                    encoding='utf-8')
    noun_map = {'阿童木': entry(ja=['アトム'], episodes=['EP001']),
                'c': 'keep'}

    agg.seed_from_mapping(noun_map, str(seed))

    assert noun_map['阿童木']['ja_forms'] == ['アトム']
    assert noun_map['阿童木']['episodes'] == ['EP001']
    assert noun_map['c'] == 'keep'


def test_extract_ep_from_sidecar(tmp_path):
    from_name = tmp_path / 'extracted_EP007.json'
    assert agg._extract_ep_from_sidecar(str(from_name)) == 'EP007'

    from_content = tmp_path / 'whatever.json'
    from_content.write_text(json.dumps({'episode': 'EP012'}), encoding='utf-8')
    assert agg._extract_ep_from_sidecar(str(from_content)) == 'EP012'

    broken = tmp_path / 'broken.json'
    broken.write_text('not json', encoding='utf-8')
    assert agg._extract_ep_from_sidecar(str(broken)) == '???'


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_main_init_seed_and_merge_sidecar(monkeypatch, tmp_path):
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'アトム': '阿童木'}), encoding='utf-8')
    sidecar = tmp_path / 'extracted_EP001.json'
    sidecar.write_text(json.dumps({
        'episode': 'EP001',
        'entities': [ent(ja=['アトム'], zh=['阿托姆'], count=3)],
        'chunks': {'chunks_total': 1, 'chunks_failed': 0},
    }, ensure_ascii=False), encoding='utf-8')
    noun_map = tmp_path / 'noun_map.json'
    out = tmp_path / 'out.json'

    monkeypatch.setattr(sys, 'argv', [
        'aggregate.py', '--map', str(noun_map), '--seed', str(seed),
        '--init', '--extracted', str(sidecar), '-o', str(out)])
    monkeypatch.setattr(agg, 'call_chat',
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError('LLM must not be called')))

    agg.main()

    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['阿童木']['ja_forms'] == ['アトム']
    assert data['阿童木']['episodes'] == ['EP001']
    assert data['阿童木']['count'] == 3


def test_main_init_requires_seed(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', [
        'aggregate.py', '--map', str(tmp_path / 'm.json'), '--init'])

    try:
        agg.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError('expected SystemExit')
