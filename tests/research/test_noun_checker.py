"""Tests for nouns/noun_checker.py — normalizers, tables, matching."""

import pytest

from nouns.noun_checker import (
    ChineseExtractor, ChineseNormalizer, JapaneseExtractor,
    JapaneseNormalizer, _dedup_overlapping, _is_valid_text,
    check_oped_consistency, check_srt, get_extractor, get_normalizer,
    match_candidates, parse_noun_table, print_summary, text_similarity,
)


def _srt(*texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> '
                      f'00:00:{i * 2 + 1:02d},000\n{text}\n')
    return '\n'.join(blocks)


# ═══════════════════════════════════════════════════════════════
# Normalizers
# ═══════════════════════════════════════════════════════════════

def test_japanese_normalizer_levels():
    assert JapaneseNormalizer.normalize('コーヒー') == 'コヒ'
    assert JapaneseNormalizer.normalize('キャ', 'medium') == 'キヤ'
    assert JapaneseNormalizer.normalize('ガ', 'lenient') == 'カ'
    assert JapaneseNormalizer.normalize('サリー', 'strict') == 'サリ'


def test_chinese_normalizer_strict_maps_traditional():
    out = ChineseNormalizer.normalize('龍')
    assert out == '龙'


def test_get_normalizer_factory():
    assert get_normalizer('ja') is JapaneseNormalizer
    assert get_normalizer('zh') is ChineseNormalizer
    assert get_normalizer('??') is JapaneseNormalizer


# ═══════════════════════════════════════════════════════════════
# Dedup / similarity
# ═══════════════════════════════════════════════════════════════

def test_dedup_keeps_longest_overlapping_candidate():
    candidates = [
        {'text': '茶水博士', 'start': 1},
        {'text': '御茶水博士', 'start': 0},
        {'text': '博士', 'start': 3},
        {'text': 'サリー', 'start': 10},
    ]
    kept = _dedup_overlapping(candidates)
    texts = [c['text'] for c in kept]
    assert '御茶水博士' in texts
    assert 'サリー' in texts
    assert '茶水博士' not in texts
    assert '博士' not in texts


def test_text_similarity_is_case_insensitive():
    assert text_similarity('ABC', 'abc') == 1.0
    assert text_similarity('サリー', 'サリ') > 0.5


# ═══════════════════════════════════════════════════════════════
# Noun table parsing
# ═══════════════════════════════════════════════════════════════

MD = """# Proper Nouns

| 日语 | 假名 | 说明 |
|---|---|---|
| サリー | さりー | 主角 |
| スミレ | すみれ | 朋友 |
"""


def test_parse_noun_table(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text(MD, encoding='utf-8')
    nouns = parse_noun_table(str(path))
    names = [n['name'] for n in nouns]
    assert names == ['サリー', 'スミレ']
    assert nouns[0]['reading'] == 'さりー'
    assert nouns[0]['name_norm'] == JapaneseNormalizer.normalize('サリー',
                                                                 'lenient')


def test_parse_noun_table_missing_returns_empty(tmp_path):
    assert parse_noun_table(str(tmp_path / 'nope.md')) == []


# ═══════════════════════════════════════════════════════════════
# Matching
# ═══════════════════════════════════════════════════════════════

def _nouns():
    return [{'name': 'サリー',
             'name_norm': JapaneseNormalizer.normalize('サリー', 'lenient')}]


def test_match_exact():
    result = match_candidates(
        [{'text': 'サリー', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サリーが来た', JapaneseNormalizer)
    assert result[0]['status'] == 'exact'
    assert result[0]['similarity'] == 1.0


def test_match_normalized_variant():
    result = match_candidates(
        [{'text': 'サーリー', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サーリーが来た', JapaneseNormalizer)
    assert result[0]['status'] == 'variant'
    assert result[0]['suggestion'] == 'サリー'


def test_match_fuzzy_and_unknown():
    fuzzy = match_candidates(
        [{'text': 'サリイ', 'start': 0, 'type': 'calling'}],
        _nouns(), 'サリイが来た', JapaneseNormalizer)
    assert fuzzy[0]['status'] in ('mismatch', 'unknown')

    unknown = match_candidates(
        [{'text': 'タロウ', 'start': 0, 'type': 'calling'}],
        _nouns(), 'タロウが来た', JapaneseNormalizer)
    assert unknown[0]['status'] == 'unknown'
    assert unknown[0]['matched'] is None


# ═══════════════════════════════════════════════════════════════
# Japanese extractor
# ═══════════════════════════════════════════════════════════════

def test_extract_katakana_with_honorific():
    candidates = JapaneseExtractor.extract('サリーちゃんが来た')
    texts = [c['text'] for c in candidates]
    assert 'サリーちゃん' in texts


def test_extract_calling_pattern():
    candidates = JapaneseExtractor.extract('おい、サリー！')
    texts = [c['text'] for c in candidates]
    assert 'サリー' in texts


def test_get_extractor_factory():
    assert get_extractor('ja') is JapaneseExtractor
    assert get_extractor('zh') is ChineseExtractor
    assert get_extractor('??') is JapaneseExtractor


def test_extract_kanji_suffix_skips_hiragana_prefix():
    found = JapaneseExtractor.extract('御茶水博士が来た')
    assert '御茶水博士' in [c['text'] for c in found]

    skipped = JapaneseExtractor.extract('は御茶水博士')
    assert '御茶水博士' not in [c['text'] for c in skipped]


def test_extract_intro_and_known_katakana():
    intro = JapaneseExtractor.extract('アトムっていうんだ')
    assert ('アトム', 'introduction') in [(c['text'], c['type'])
                                          for c in intro]

    known = JapaneseExtractor.extract('サリーが来た', {'サリー'})
    assert ('サリー', 'known_katakana') in [(c['text'], c['type'])
                                            for c in known]


def test_extract_filters_common_katakana():
    candidates = JapaneseExtractor.extract('テレビちゃんが来た')
    assert 'テレビちゃん' not in [c['text'] for c in candidates]


# ═══════════════════════════════════════════════════════════════
# Chinese extractor
# ═══════════════════════════════════════════════════════════════

def test_chinese_extract_suffix_calling_intro_quoted():
    suffix = ChineseExtractor.extract('阿童木先生来了')
    assert ('阿童木先生', 'name_suffix') in [(c['text'], c['type'])
                                             for c in suffix]

    calling = ChineseExtractor.extract('喂，阿童木！')
    assert ('阿童木', 'calling') in [(c['text'], c['type'])
                                     for c in calling]

    intro = ChineseExtractor.extract('我叫阿童木')
    assert ('阿童木', 'introduction') in [(c['text'], c['type'])
                                          for c in intro]

    quoted = ChineseExtractor.extract('「阿童木」来了')
    assert ('阿童木', 'quoted') in [(c['text'], c['type'])
                                    for c in quoted]


def test_chinese_extract_known_and_common_words():
    known = ChineseExtractor.extract('莎莉', {'莎莉'})
    assert ('莎莉', 'known_hanzi') in [(c['text'], c['type'])
                                       for c in known]

    common = ChineseExtractor.extract('我们先生来了')
    assert '我们先生' not in [c['text'] for c in common]


# ═══════════════════════════════════════════════════════════════
# Validity / summary
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize('text, lang, valid', [
    ('こんにちは', 'ja', True),
    ('hello', 'ja', False),
    ('あ', 'ja', False),
    ('你好世界', 'zh', True),
    ('hello world', 'zh', False),
])
def test_is_valid_text(text, lang, valid):
    assert _is_valid_text(text, lang) is valid


def test_print_summary_marks_attention_and_truncates(capsys):
    results = [{'status': 'mismatch', 'cue_start': '00:00:01,000',
                'candidate': 'サリイ', 'suggestion': 'サリー',
                'context': '...サリイが来た...'}]
    results += [{'status': 'unknown', 'cue_start': f'00:00:{i:02d},000',
                 'candidate': f'名{i}', 'suggestion': None,
                 'context': 'ctx'} for i in range(2, 23)]
    report = {'srt_file': 'EP001.srt', 'lang': 'ja',
              'known_nouns_count': 3, 'cues_scanned': 10,
              'findings': len(results),
              'stats': {'exact': 0, 'variant': 0, 'mismatch': 1,
                        'unknown': 21},
              'results': results}

    print_summary(report)

    out = capsys.readouterr().out
    assert '[FIX] 00:00:01,000 | サリイ → サリー' in out
    assert '... and 2 more' in out


# ═══════════════════════════════════════════════════════════════
# check_srt / check_oped_consistency
# ═══════════════════════════════════════════════════════════════

def _write_table(tmp_path, rows):
    path = tmp_path / 'proper-nouns.md'
    path.write_text('| 日语 | 假名 | 说明 |\n|---|---|---|\n'
                    + '\n'.join(rows) + '\n', encoding='utf-8')
    return path


def test_check_srt_exact_and_variant(tmp_path):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(_srt('おい、サリー！', 'おい、サーリー！'),
                   encoding='utf-8')
    table = _write_table(tmp_path, ['| サリー | さりー | 主角 |'])

    report = check_srt(str(srt), str(table), lang='ja')

    assert report['cues_scanned'] == 2
    assert report['stats'] == {'exact': 1, 'variant': 1}
    assert report['results'][0]['cue_start'] == report['results'][0]['cue_start']


def test_check_srt_without_table(tmp_path):
    srt = tmp_path / 'EP001.srt'
    srt.write_text(_srt('おい、サリー！'), encoding='utf-8')
    report = check_srt(str(srt), str(tmp_path / 'nope.md'), lang='ja')
    assert report == {'error': 'No known nouns found in table'}


def _write_timed_srt(path, entries):
    blocks = []
    for i, (start, end, text) in enumerate(entries, 1):
        blocks.append(f'{i}\n{start} --> {end}\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')


def test_check_oped_consistency_needs_two_episodes(tmp_path):
    _write_timed_srt(tmp_path / 'EP001.srt', [
        ('00:00:01,000', '00:00:02,000', 'サリーの歌'),
    ])
    result = check_oped_consistency(str(tmp_path), op_boundary=10,
                                    ed_boundary=10)
    assert result['summary']['error'].startswith('Need at least 2')
    assert result['fixes'] == []


def test_check_oped_consistency_flags_variants(tmp_path):
    for episode, op_text in (('EP001', 'サーリーの歌'),
                             ('EP002', 'サリーの歌'),
                             ('EP003', 'サリーの歌')):
        _write_timed_srt(tmp_path / f'{episode}.srt', [
            ('00:00:01,000', '00:00:02,000', op_text),
            ('00:01:35,000', '00:01:36,000', 'エンディング'),
            ('00:01:40,000', '00:01:41,000', '本編'),
        ])

    result = check_oped_consistency(str(tmp_path), op_boundary=10,
                                    ed_boundary=10)

    assert result['summary']['episodes'] == 3
    assert result['summary']['op_groups'] == 1
    assert result['summary']['op_variants_found'] == 1
    assert result['summary']['total_fixes'] == 1
    fix = result['fixes'][0]
    assert fix['file'] == 'EP001.srt'
    assert fix['original'] == 'サーリーの歌'
    assert fix['replacement'] == 'サリーの歌'
    assert 'OP 歌词统一' in fix['note']


def test_check_oped_consistency_skips_noise_canonical(tmp_path):
    for episode, op_text in (('EP001', 'ABC'), ('EP002', 'ABC'),
                             ('EP003', 'ABD')):
        _write_timed_srt(tmp_path / f'{episode}.srt', [
            ('00:00:01,000', '00:00:02,000', op_text),
            ('00:01:40,000', '00:01:41,000', '本編'),
        ])

    result = check_oped_consistency(str(tmp_path), op_boundary=10,
                                    ed_boundary=10)

    assert result['summary']['total_fixes'] == 0
