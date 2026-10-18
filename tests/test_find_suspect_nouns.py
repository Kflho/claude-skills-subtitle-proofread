"""Tests for nouns/find_suspect_nouns.py — known-term loading and detection."""

import json
import os

from nouns import find_suspect_nouns as fsn


def _srt(*texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n'
                      f'{text}\n')
    return '\n'.join(blocks)


# ═══════════════════════════════════════════════════════════════
# Known term loading
# ═══════════════════════════════════════════════════════════════

def test_load_known_terms_from_glossary_and_mappings(tmp_path):
    glossary = tmp_path / 'proper-nouns.md'
    glossary.write_text(
        '| 日语 | 出现次数 |\n|------|---------|\n| アトム | 12 |\n',
        encoding='utf-8')
    mappings = tmp_path / 'noun_mappings.json'
    mappings.write_text(json.dumps({'サリー': '莎莉'}, ensure_ascii=False),
                        encoding='utf-8')

    known_ja, known_zh, ja_to_zh = fsn._load_known_terms(
        str(glossary), str(mappings), lang='ja')

    assert 'アトム' in known_ja          # from glossary table
    assert 'サリー' in known_ja          # from mappings
    assert '莎莉' in known_zh
    assert ja_to_zh == {'サリー': '莎莉'}
    assert 'テレビ' in known_ja          # built-in COMMON_KATAKANA


def test_load_known_terms_external_zh_blacklist(tmp_path, monkeypatch):
    monkeypatch.setattr(fsn, '_get_jieba', lambda: None)
    blacklist = tmp_path / 'zh_blacklist.json'
    blacklist.write_text(json.dumps(['机器人', '科学'], ensure_ascii=False),
                         encoding='utf-8')

    _, known_zh, _ = fsn._load_known_terms(
        lang='zh', zh_blacklist_path=str(blacklist))

    assert '机器人' in known_zh and '科学' in known_zh


def test_load_known_terms_without_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(fsn, '_get_jieba', lambda: None)
    known_ja, known_zh, ja_to_zh = fsn._load_known_terms()
    assert ja_to_zh == {}
    assert known_zh == set()
    assert 'テレビ' in known_ja


# ═══════════════════════════════════════════════════════════════
# Name context heuristics
# ═══════════════════════════════════════════════════════════════

def test_is_in_name_context_ja():
    assert fsn._is_in_name_context('アトムさん、来て', 'アトム', 'ja') == (
        True, 'honorific_suffix')
    assert fsn._is_in_name_context('俺はアトムだ', 'アトム', 'ja')[0] is True
    assert fsn._is_in_name_context('アトムっていうんだ', 'アトム', 'ja') == (
        True, 'intro_after')
    assert fsn._is_in_name_context('おい アトム', 'アトム', 'ja') == (
        True, 'calling')
    assert fsn._is_in_name_context('アトムが飛んだ', 'アトム', 'ja') == (
        False, '')


def test_is_in_name_context_zh():
    assert fsn._is_in_name_context('阿童木博士来了', '阿童木', 'zh') == (
        True, 'title_suffix')
    assert fsn._is_in_name_context('我叫阿童木', '阿童木', 'zh') == (
        True, 'intro_pattern')
    assert fsn._is_in_name_context('我的阿童木', '阿童木', 'zh') == (
        True, 'possessive')
    assert fsn._is_in_name_context('飞雄飞了', '飞雄', 'zh') == (False, '')


# ═══════════════════════════════════════════════════════════════
# Readings / segmentation fallbacks (no janome / jieba needed)
# ═══════════════════════════════════════════════════════════════

def test_norm_ja_reading_fallback(monkeypatch):
    monkeypatch.setattr(fsn, '_get_janome', lambda: None)
    assert fsn._norm_ja_reading('スーパー') == 'スパ'
    assert fsn._norm_ja_reading('キャラ') == 'キヤラ'
    assert fsn._norm_ja_reading('トビ') == 'トビ'


def test_segment_ja_without_janome(monkeypatch):
    monkeypatch.setattr(fsn, '_get_janome', lambda: None)
    assert fsn._segment_ja('アトムが飛んだ') == []


def test_segment_zh_fallback_ngrams(monkeypatch):
    monkeypatch.setattr(fsn, '_get_jieba', lambda: None)

    tokens = fsn._segment_zh('阿童木来了')

    assert {'阿童', '阿童木', '童木', '来了'} <= set(tokens)
    assert all(any('\u4e00' <= ch <= '\u9fff' for ch in t) for t in tokens)
    assert fsn._segment_zh('abc 123') == []


# ═══════════════════════════════════════════════════════════════
# Related-group merging
# ═══════════════════════════════════════════════════════════════

def _group(norm, variants, canonical='[未知]', source_ja='x', reason='同源异译'):
    return {
        'source_ja_norm': norm,
        'source_ja': source_ja,
        'suspected_canonical': canonical,
        'reason': reason,
        'variants': [{'text': v} for v in variants],
    }


def test_merge_related_groups_by_substring_reading():
    groups = [_group('トビ', ['阿童木', '阿托姆'], source_ja='トビ'),
              _group('トビラ', ['飞雄', '飞夫'], source_ja='トビラ'),
              _group('サリ', ['莎莉', '沙莉'], source_ja='サリー')]

    merged = fsn._merge_related_groups(groups)

    assert len(merged) == 2
    tobi = next(g for g in merged if g['source_ja_norm'] == 'トビ')
    assert len(tobi['variants']) == 4
    assert '模糊匹配' in tobi['reason']
    assert tobi['source_ja'] == 'トビ, トビラ'


def test_merge_related_groups_keeps_known_canonical():
    groups = [_group('トビ', ['阿童木', '阿托姆'], canonical='[未知]'),
              _group('トビラ', ['飞雄', '飞夫'], canonical='阿童木')]

    merged = fsn._merge_related_groups(groups)

    assert merged[0]['suspected_canonical'] == '阿童木'


def test_merge_related_groups_drops_single_variant_groups():
    groups = [_group('ア', ['甲', '乙']), _group('イ', ['丙'])]

    merged = fsn._merge_related_groups(groups)

    assert [g['source_ja_norm'] for g in merged] == ['ア']


def test_merge_related_groups_single_group_returns_as_is():
    single = [_group('ア', ['甲'])]
    assert fsn._merge_related_groups(single) == single


# ═══════════════════════════════════════════════════════════════
# find_suspect_nouns end-to-end (segmentation mocked)
# ═══════════════════════════════════════════════════════════════

def _mock_ja_tokens(monkeypatch):
    """Treat each whitespace-separated word as a token."""
    monkeypatch.setattr(fsn, '_segment_ja', lambda text: text.split())


def test_find_suspect_nouns_single_file_keeps_katakana(monkeypatch, tmp_path):
    (tmp_path / 'EP001.srt').write_text(
        _srt('アトム 今日 きた', 'サリー アトム'), encoding='utf-8')
    _mock_ja_tokens(monkeypatch)

    result = fsn.find_suspect_nouns(str(tmp_path), lang='ja')

    texts = [s['text'] for s in result['singletons']]
    assert 'アトム' in texts
    assert '今日' not in texts          # common kanji excluded
    assert result['singletons'][0]['reason'] == 'unknown_suspect'
    atom = next(s for s in result['singletons'] if s['text'] == 'アトム')
    assert atom['count'] == 2
    assert atom['eps'] == ['EP001']
    assert atom['contexts']


def test_find_suspect_nouns_excludes_known_terms(monkeypatch, tmp_path):
    (tmp_path / 'EP001.srt').write_text(
        _srt('サリー アトム'), encoding='utf-8')
    mappings = tmp_path / 'mappings.json'
    mappings.write_text(json.dumps({'サリー': '莎莉'}, ensure_ascii=False),
                        encoding='utf-8')
    _mock_ja_tokens(monkeypatch)

    result = fsn.find_suspect_nouns(str(tmp_path), lang='ja',
                                    mappings_path=str(mappings))

    texts = [s['text'] for s in result['singletons']]
    assert 'アトム' in texts
    assert 'サリー' not in texts


def test_find_suspect_nouns_limit_and_max_singletons(monkeypatch, tmp_path):
    (tmp_path / 'EP001.srt').write_text(_srt('アトム サリー'),
                                        encoding='utf-8')
    (tmp_path / 'EP002.srt').write_text(_srt('ウラン'), encoding='utf-8')
    _mock_ja_tokens(monkeypatch)

    result = fsn.find_suspect_nouns(str(tmp_path), lang='ja', limit=1,
                                    max_singletons=1)

    assert len(result['singletons']) == 1
    assert result['singletons'][0]['eps'] == ['EP001']


def test_find_suspect_nouns_translation_mode_clusters(monkeypatch, tmp_path):
    (tmp_path / 'EP001.srt').write_text(_srt('阿童木 来了'), encoding='utf-8')
    source_dir = tmp_path / 'ja'
    source_dir.mkdir()
    (source_dir / 'EP001.srt').write_text(_srt('アトム きた'), encoding='utf-8')
    monkeypatch.setattr(fsn, '_segment_zh', lambda text: text.split())
    monkeypatch.setattr(fsn, '_prime_jieba_with_known', lambda terms: None)
    monkeypatch.setattr(fsn, '_get_jieba', lambda: None)
    monkeypatch.setattr(
        fsn, '_cluster_by_source',
        lambda suspects, target_dir, source_dir, ja_to_zh, known_ja: [{
            'source_ja': 'アトム',
            'source_ja_norm': 'アトム',
            'suspected_canonical': '阿童木',
            'reason': '同源异译',
            'variants': [{'text': '阿童木'}],
        }])

    result = fsn.find_suspect_nouns(str(tmp_path), lang='zh',
                                    mode='translation',
                                    source_dir=str(source_dir))

    assert len(result['groups']) == 1
    assert '阿童木' not in [s['text'] for s in result['singletons']]


# ═══════════════════════════════════════════════════════════════
# Source clustering
# ═══════════════════════════════════════════════════════════════

def _patch_tokenizers(monkeypatch):
    monkeypatch.setattr(fsn, '_segment_zh', lambda text: text.split())
    monkeypatch.setattr(fsn, '_segment_ja', lambda text: text.split())
    monkeypatch.setattr(fsn, '_norm_ja_reading', lambda term: term)


def test_cluster_by_source_groups_variants(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    source = tmp_path / 'ja'
    target.mkdir()
    source.mkdir()
    (target / 'EP001.srt').write_text(_srt('阿托姆 来了'), encoding='utf-8')
    (target / 'EP002.srt').write_text(_srt('阿圖姆 来了'), encoding='utf-8')
    (source / 'EP001.srt').write_text(_srt('アトム きた'), encoding='utf-8')
    (source / 'EP002.srt').write_text(_srt('アトム きた'), encoding='utf-8')
    _patch_tokenizers(monkeypatch)

    groups = fsn._cluster_by_source(
        [{'text': '阿托姆'}, {'text': '阿圖姆'}],
        str(target), str(source), {'アトム': '阿童木'}, set())

    assert len(groups) == 1
    group = groups[0]
    assert group['suspected_canonical'] == '阿童木'
    assert group['source_ja'] == 'アトム'
    assert {v['text'] for v in group['variants']} == {'阿托姆', '阿圖姆'}
    atuomu = [v for v in group['variants'] if v['text'] == '阿托姆'][0]
    assert atuomu['locations'][0]['file'] == 'EP001.srt'


def test_cluster_by_source_skips_unmapped_tokens(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    source = tmp_path / 'ja'
    target.mkdir()
    source.mkdir()
    (target / 'EP001.srt').write_text(_srt('阿托姆 来了'), encoding='utf-8')
    (target / 'EP002.srt').write_text(_srt('阿圖姆 来了'), encoding='utf-8')
    (source / 'EP001.srt').write_text(_srt('サリー きた'), encoding='utf-8')
    (source / 'EP002.srt').write_text(_srt('サリー きた'), encoding='utf-8')
    _patch_tokenizers(monkeypatch)

    groups = fsn._cluster_by_source(
        [{'text': '阿托姆'}, {'text': '阿圖姆'}],
        str(target), str(source), {'アトム': '阿童木'}, set())

    assert groups == []


def test_cluster_by_source_needs_two_variants(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    source = tmp_path / 'ja'
    target.mkdir()
    source.mkdir()
    (target / 'EP001.srt').write_text(_srt('阿托姆 来了'), encoding='utf-8')
    (source / 'EP001.srt').write_text(_srt('アトム きた'), encoding='utf-8')
    _patch_tokenizers(monkeypatch)

    assert fsn._cluster_by_source(
        [{'text': '阿托姆'}], str(target), str(source),
        {'アトム': '阿童木'}, set()) == []


# ═══════════════════════════════════════════════════════════════
# Correction search / apply
# ═══════════════════════════════════════════════════════════════

def test_search_corrections_reports_hits_and_source_confirmation(tmp_path,
                                                                monkeypatch):
    target = tmp_path / 'zh'
    source = tmp_path / 'ja'
    target.mkdir()
    source.mkdir()
    (target / 'EP001.srt').write_text(_srt('阿托姆 来了'), encoding='utf-8')
    (target / 'EP002.srt').write_text(_srt('阿托姆 走了'), encoding='utf-8')
    (source / 'EP001.srt').write_text(_srt('アトム きた'), encoding='utf-8')
    (source / 'EP002.srt').write_text(_srt('アトム いった'), encoding='utf-8')
    _patch_tokenizers(monkeypatch)
    out = tmp_path / 'hits.json'
    corrections = [{'wrong': ['阿托姆'], 'correct': '阿童木',
                    'source_ja': 'アトム'}]

    results = fsn.search_corrections(
        str(target), corrections, source_dir=str(source), lang='zh',
        output_path=str(out))

    assert len(results) == 1
    assert results[0]['total_hits'] == 2
    assert results[0]['eps_affected'] == ['EP001', 'EP002']
    hit = results[0]['hits'][0]
    assert hit['suggested_text'] == '阿童木 来了'
    assert hit['source_confirmed'] is True
    saved = json.loads(out.read_text(encoding='utf-8'))
    assert saved[0]['total_hits'] == 2


def test_apply_correction_fixes_replaces_and_backs_up(tmp_path):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(_srt('阿托姆 来了', '别的台词'), encoding='utf-8')
    results = [{
        'correction': {'wrong': ['阿托姆'], 'correct': '阿童木'},
        'hits': [{
            'ep': 'EP001', 'cue_index': 0,
            'original_text': '阿托姆 来了',
            'suggested_text': '阿童木 来了',
        }],
    }]

    fsn._apply_correction_fixes(str(tmp_path), results)

    text = srt.read_text(encoding='utf-8')
    assert '阿童木 来了' in text
    assert '别的台词' in text
    assert (tmp_path / 'EP001.srt.bak').exists()


def test_apply_correction_fixes_skips_when_text_changed(tmp_path, capsys):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(_srt('已经改过了'), encoding='utf-8')
    results = [{
        'correction': {'wrong': ['阿托姆'], 'correct': '阿童木'},
        'hits': [{'ep': 'EP001', 'cue_index': 0,
                  'original_text': '阿托姆 来了',
                  'suggested_text': '阿童木 来了'}],
    }]

    fsn._apply_correction_fixes(str(tmp_path), results)

    assert '阿童木' not in srt.read_text(encoding='utf-8')
    assert not (tmp_path / 'EP001.srt.bak').exists()


# ═══════════════════════════════════════════════════════════════
# Candidate formatting
# ═══════════════════════════════════════════════════════════════

def test_format_candidates_flattens_groups_and_singletons(tmp_path):
    locations = [
        {'file': f'EP{i:03d}.srt', 'timestamp': '00:00:01,000',
         'text': '阿托姆来了'} for i in range(1, 13)
    ]
    raw = {
        'groups': [
            {'source_ja': 'アトム', 'suspected_canonical': '阿童木',
             'variants': [
                 {'text': '阿童木', 'locations': locations[:1]},
                 {'text': '阿托姆', 'locations': locations},
                 {'text': '阿圖姆', 'locations': locations[:3]},
             ]},
            {'source_ja': 'x', 'suspected_canonical': '[未知: トビ]',
             'variants': [{'text': '飞雄', 'locations': []},
                          {'text': '飞夫', 'locations': []}]},
            {'source_ja': 'y', 'suspected_canonical': '莎莉',
             'variants': [{'text': '莎莉', 'locations': locations[:1]}]},
        ],
        'singletons': [
            {'text': 'ウラン', 'count': 5,
             'contexts': [{'ep': 'EP001', 'cue_start': '00:00:02,000',
                           'cue_text': 'ウランが来た'}]},
            {'text': '阿童木', 'count': 2, 'contexts': []},
            {'text': 'あ', 'count': 1, 'contexts': []},
        ],
    }
    out = tmp_path / 'candidates.json'

    result = fsn._format_candidates(
        raw, {'アトム': '阿童木'}, str(out))

    assert result['stats'] == {'total': 2, 'inconsistency': 1,
                               'unknown_suspect': 1}
    inconsistency = result['candidates'][0]
    assert inconsistency['type'] == 'inconsistency'
    assert inconsistency['zh_canonical_in_mappings'] == '阿童木'
    wrong = [a for a in inconsistency['zh_appearances']
             if a['text'] == '阿托姆'][0]
    assert wrong['count'] == 12
    assert len(wrong['sample_locations']) == 10
    assert len(inconsistency['sample_contexts']) == 5

    unknown = result['candidates'][1]
    assert unknown['type'] == 'unknown_suspect'
    assert unknown['zh_term'] == 'ウラン'
    assert unknown['frequency'] == 5
    assert json.loads(out.read_text(encoding='utf-8'))['stats']['total'] == 2
