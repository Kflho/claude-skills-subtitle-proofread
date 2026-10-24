"""Tests for fix/oped_fixer.py — cross-episode classification, lyric-only fixes."""

import json
from collections import Counter

import pytest

from fix import oped_fixer as of
from fix.oped_fixer import OpedFixer, TimeCluster


def _write_srt(path, cues):
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f'{i}\n{start} --> {end}\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')


def _entry(ep, text, start_s=1.0, index=0):
    return {'ep': ep, 'text': text, 'start': '00:00:01,000',
            'start_s': start_s, 'end': '00:00:02,000',
            'end_s': start_s + 1, 'index': index}


def _cluster(region='OP', bucket_start=1.0, texts=(), lang='ja'):
    cluster = TimeCluster(bucket_start=bucket_start, region=region)
    for i, text in enumerate(texts):
        cluster.entries.append(_entry(f'EP{i:03d}.srt', text, bucket_start, i))
    cluster.variants = dict(Counter(texts))
    # Mirrors OpedFixer.cluster(): counts drive the classification
    for text, count in cluster.variants.items():
        if of._is_music_marker(text):
            continue
        if of._is_noise(text, lang):
            cluster.noise_count += count
        elif of._is_valid_text(text, lang):
            cluster.valid_count += count
    return cluster


# ═══════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════

def test_text_similarity():
    assert of._text_similarity('同じ歌詞', '同じ歌詞') == 1.0
    assert of._text_similarity('あいうえお', 'かきくけこ') == 0.0
    assert of._text_similarity('', 'x') == 0.0
    assert 0 < of._text_similarity('同じ歌詞A', '同じ歌詞B') < 1


@pytest.mark.parametrize('text, lang, noise', [
    ('', 'ja', True),
    ('...', 'ja', True),
    ('ABC', 'ja', True),
    ('ああああ', 'ja', True),
    ('はい', 'ja', False),
    ('こんにちは', 'ja', False),
    ('你好', 'zh', False),
    ('Hello world', 'zh', True),
    ('Hi', 'en', False),
    ('你好世界', 'en', True),
])
def test_is_noise(text, lang, noise):
    assert of._is_noise(text, lang) is noise


@pytest.mark.parametrize('text, lang, valid', [
    ('こんにちは', 'ja', True),
    ('Hello', 'ja', False),
    ('日', 'ja', False),
    ('你好世界', 'zh', True),
    ('Hello', 'zh', False),
    ('Hello there', 'en', True),
    ('你好世界', 'en', False),
])
def test_is_valid_text(text, lang, valid):
    assert of._is_valid_text(text, lang) is valid


def test_is_music_marker():
    assert of._is_music_marker('[音楽]') is True
    assert of._is_music_marker('[拍手]') is True
    assert of._is_music_marker('音楽') is False


def test_detect_script_and_lang():
    assert of._detect_script('こんにちは') == 'kana'
    assert of._detect_script('你好世界') == 'cjk'
    assert of._detect_script('Привет') == 'cyrillic'
    assert of._detect_script('Hello') == 'latin'
    assert of._detect_script('   ') == 'unknown'
    assert of._script_to_lang('kana') == 'ja'
    assert of._script_to_lang('cyrillic') == 'ru'


def test_detect_corpus_lang():
    ja = [{'text': 'こんにちは'}, {'text': 'さようなら'}]
    zh = [{'text': '你好世界'}, {'text': '再见'}]
    noise = [{'text': '[音楽]'}, {'text': 'ABC'}]

    assert of._detect_corpus_lang(ja) == 'ja'
    assert of._detect_corpus_lang(zh) == 'zh'
    assert of._detect_corpus_lang(noise) == '??'
    assert of._detect_corpus_lang([]) == '??'


def test_scripts_match():
    assert of._scripts_match('cjk', 'zh') is True
    assert of._scripts_match('kana', 'zh') is False
    assert of._scripts_match('kana', 'ja') is True
    assert of._scripts_match('cyrillic', 'ru') is True
    assert of._scripts_match('latin', 'en') is False


# ═══════════════════════════════════════════════════════════════
# Init / boundaries
# ═══════════════════════════════════════════════════════════════

def test_boundaries_from_api_override_defaults():
    fixer = OpedFixer('.', boundaries={
        'confidence': 'api', 'op_end_s': 45.0, 'ed_end_s': 60.0})
    assert (fixer.op_boundary, fixer.ed_boundary) == (45.0, 60.0)
    assert fixer._boundaries_from_api is True


def test_boundaries_fallback_keeps_defaults():
    fixer = OpedFixer('.', op_boundary=10, ed_boundary=20,
                      boundaries={'confidence': 'fallback', 'op_end_s': 99})
    assert (fixer.op_boundary, fixer.ed_boundary) == (10, 20)
    assert fixer._boundaries_from_api is False


def test_boundaries_api_without_ed_keeps_default():
    fixer = OpedFixer('.', ed_boundary=20, boundaries={
        'confidence': 'api', 'op_end_s': 45.0, 'ed_end_s': None})
    assert fixer.ed_boundary == 20


# ═══════════════════════════════════════════════════════════════
# collect — only OP/ED windows, never main dialogue
# ═══════════════════════════════════════════════════════════════

def test_collect_splits_regions_and_skips_main_dialogue(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:01,000', '00:00:02,000', '歌'),
        ('00:03:20,000', '00:03:21,000', '本編セリフ'),
        ('00:21:50,000', '00:21:51,000', 'エンディング'),
    ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=10)
    fixer.collect()

    ep = fixer.episodes['EP001.srt']
    assert [c['text'] for c in ep['op_cues']] == ['歌']
    assert [c['text'] for c in ep['ed_cues']] == ['エンディング']
    # 本編セリフ at 200s is in neither window
    assert '本編セリフ' not in [c['text'] for c in ep['op_cues']]
    assert '本編セリフ' not in [c['text'] for c in ep['ed_cues']]


def test_collect_without_files(tmp_path, capsys):
    fixer = OpedFixer(str(tmp_path))
    fixer.collect()
    assert fixer.episodes == {}
    assert 'No subtitle files found' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# cluster / classify
# ═══════════════════════════════════════════════════════════════

def test_find_bucket_uses_time_tolerance():
    buckets = {('OP', 1.0): {}}
    assert OpedFixer('.')._find_bucket(2.5, 'OP', buckets) == 1.0
    assert OpedFixer('.')._find_bucket(4.0, 'OP', buckets) is None
    assert OpedFixer('.')._find_bucket(1.5, 'ED', buckets) is None


def test_classify_cluster_instrumental_all_noise():
    fixer = OpedFixer('.')
    cluster = _cluster(texts=['ABC', 'XYZ', 'QQQ'])
    fixer._classify_cluster(cluster)

    assert cluster.is_instrumental is True
    assert fixer.instrumental_clusters == [cluster]


def test_classify_cluster_dialogue_unique_text():
    fixer = OpedFixer('.')
    cluster = _cluster(texts=['今日はいい天気', '明日は雨だよ', '昨日は寒かった'])
    fixer._classify_cluster(cluster)

    assert cluster.is_dialogue is True
    assert fixer.dialogue_clusters == [cluster]


def test_classify_cluster_vocal_similar_variants():
    fixer = OpedFixer('.')
    cluster = _cluster(texts=['同じ歌詞A', '同じ歌詞B', '同じ歌詞A'])
    fixer._classify_cluster(cluster)

    assert fixer.vocal_clusters == [cluster]
    assert cluster.is_instrumental is False
    assert cluster.is_dialogue is False


def test_cluster_requires_min_episodes(tmp_path):
    for i in range(3):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', '同じ歌詞A'),
        ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=10,
                      min_episodes=4)
    fixer.collect()
    fixer.cluster()

    assert fixer.clusters == []


def test_cluster_identical_valid_text_needs_no_fix(tmp_path):
    for i in range(3):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', '同じ歌詞A'),
        ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=0,
                      min_episodes=3)
    fixer.collect()
    fixer.cluster()

    assert len(fixer.clusters) == 1
    # All episodes already agree → nothing to fix
    assert fixer.vocal_clusters == []
    assert fixer.instrumental_clusters == []
    assert fixer.dialogue_clusters == []


def test_cluster_variants_across_episodes_are_vocal(tmp_path):
    texts = ['同じ歌詞A', '同じ歌詞B', '同じ歌詞A']
    for i, text in enumerate(texts):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', text),
        ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=0,
                      min_episodes=3)
    fixer.collect()
    fixer.cluster()

    assert len(fixer.vocal_clusters) == 1
    assert len(fixer.vocal_clusters[0].entries) == 3


# ═══════════════════════════════════════════════════════════════
# Auto fixes (instrumental only)
# ═══════════════════════════════════════════════════════════════

def test_generate_auto_fixes_replaces_noise_keeps_valid():
    fixer = OpedFixer('.')
    cluster = _cluster(texts=['ABC', '[音楽]', 'こんにちは'])
    fixer.instrumental_clusters = [cluster]

    fixes = fixer.generate_auto_fixes()

    assert len(fixes) == 1
    assert fixes[0]['replacement'] == '[音楽]'
    assert fixes[0]['original'] == 'ABC'
    assert fixes[0]['action'] == 'replace_text'
    assert '器楽区間' in fixes[0]['note']


# ═══════════════════════════════════════════════════════════════
# AI review candidates
# ═══════════════════════════════════════════════════════════════

def test_generate_ai_review_candidate_fields():
    fixer = OpedFixer('.')
    fixer.vocal_clusters = [_cluster(texts=['歌詞A', '歌詞B', '歌詞A'])]

    review = fixer.generate_ai_review()

    assert review['total_groups'] == 1
    assert review['op_groups'] == 1 and review['ed_groups'] == 0
    candidate = review['candidates'][0]
    assert candidate['suggested_canonical'] == '歌詞A'
    assert candidate['suggested_confidence'] == pytest.approx(2 / 3)
    assert candidate['region'] == 'OP'
    assert candidate['episode_count'] == 3
    assert len(candidate['sample_times']) == 3
    assert candidate['canonical'] == ''
    assert candidate['needs_translation'] is False


def test_generate_ai_review_fills_reference_when_same_language():
    fixer = OpedFixer('.', lang='ja')
    fixer.vocal_clusters = [_cluster(texts=['歌詞A', '歌詞B'])]
    fixer.reference_texts[('OP', 1.0)] = '正しい歌詞'

    review = fixer.generate_ai_review()

    candidate = review['candidates'][0]
    assert candidate['reference_text'] == '正しい歌詞'
    assert candidate['canonical'] == '正しい歌詞'
    assert review['auto_canonical_from_reference'] == 1
    assert candidate['needs_translation'] is False


def test_generate_ai_review_requires_translation_for_foreign_reference():
    fixer = OpedFixer('.', lang='zh')
    fixer.vocal_clusters = [_cluster(texts=['歌詞A', '歌詞B'])]
    fixer.reference_texts[('OP', 1.0)] = 'Правильный текст'

    review = fixer.generate_ai_review()

    candidate = review['candidates'][0]
    assert candidate['canonical'] == ''
    assert candidate['needs_translation'] is True
    assert review['needs_translation'] == 1


def test_generate_ai_review_skips_all_noise_clusters():
    fixer = OpedFixer('.')
    fixer.vocal_clusters = [_cluster(texts=['ABC', 'XYZ'])]

    review = fixer.generate_ai_review()

    assert review['total_groups'] == 0


# ═══════════════════════════════════════════════════════════════
# AI fixes
# ═══════════════════════════════════════════════════════════════

def _write_review(path, candidates):
    path.write_text(json.dumps({'candidates': candidates},
                               ensure_ascii=False), encoding='utf-8')


def test_apply_ai_fixes_replaces_variants_with_canonical(tmp_path):
    fixer = OpedFixer('.')
    fixer.vocal_clusters = [_cluster(texts=['歌詞A', '歌詞B', '歌詞A'])]
    review = tmp_path / 'review.json'
    _write_review(review, [{'region': 'OP', 'time_position_s': 1.0,
                            'canonical': '歌詞A'}])

    fixes = fixer.apply_ai_fixes(str(review))

    assert len(fixes) == 1
    assert fixes[0]['original'] == '歌詞B'
    assert fixes[0]['replacement'] == '歌詞A'
    assert '歌詞統一' in fixes[0]['note']


def test_apply_ai_fixes_instrumental_marker(tmp_path):
    fixer = OpedFixer('.')
    fixer.vocal_clusters = [_cluster(texts=['ABC', '[音楽]'])]
    review = tmp_path / 'review.json'
    _write_review(review, [{'region': 'OP', 'time_position_s': 1.0,
                            'canonical': '__INSTRUMENTAL__'}])

    fixes = fixer.apply_ai_fixes(str(review))

    assert len(fixes) == 1
    assert fixes[0]['replacement'] == '[音楽]'
    assert 'AI判定:器楽' in fixes[0]['note']


def test_apply_ai_fixes_ignores_empty_and_unknown(tmp_path):
    fixer = OpedFixer('.')
    fixer.vocal_clusters = [_cluster(texts=['歌詞A', '歌詞B'])]
    review = tmp_path / 'review.json'
    _write_review(review, [
        {'region': 'OP', 'time_position_s': 1.0, 'canonical': ''},
        {'region': 'OP', 'time_position_s': 99.0, 'canonical': 'X'},
    ])

    assert fixer.apply_ai_fixes(str(review)) == []


# ═══════════════════════════════════════════════════════════════
# run
# ═══════════════════════════════════════════════════════════════

def test_run_needs_at_least_two_episodes(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:01,000', '00:00:02,000', 'ABC')])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=10)

    result = fixer.run()

    assert result['fixes'] == []
    assert result['summary']['error'] == 'Need ≥2 episodes'


def test_run_auto_fixes_and_dedup(tmp_path):
    for i in range(2):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', 'ABC')])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=0,
                      min_episodes=2)

    result = fixer.run(auto_only=True)

    assert result['summary']['episodes'] == 2
    assert result['summary']['instrumental_clusters'] == 1
    assert result['summary']['auto_fixes'] == 2
    assert result['summary']['total_fixes'] == 2
    assert {f['file'] for f in result['fixes']} == {'EP000.srt', 'EP001.srt'}


def test_run_writes_ai_review_file(tmp_path):
    for i, text in enumerate(['歌詞A', '歌詞B']):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', text),
        ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=0,
                      min_episodes=2)
    review_path = tmp_path / 'review.json'

    result = fixer.run(ai_review_output=str(review_path))

    review = json.loads(review_path.read_text(encoding='utf-8'))
    assert review['total_groups'] == 1
    assert result['summary']['auto_fixes'] == 0


def test_run_applies_ai_review_file(tmp_path):
    for i, text in enumerate(['歌詞A', '歌詞B']):
        _write_srt(tmp_path / f'EP{i:03d}.srt', [
            ('00:00:01,000', '00:00:02,000', text),
        ])
    fixer = OpedFixer(str(tmp_path), op_boundary=10, ed_boundary=0,
                      min_episodes=2)
    review = tmp_path / 'review.json'
    _write_review(review, [{'region': 'OP', 'time_position_s': 1.0,
                            'canonical': '正しい歌詞'}])

    result = fixer.run(ai_review_input=str(review))

    assert result['summary']['ai_fixes'] == 2
    assert {f['replacement'] for f in result['fixes']} == {'正しい歌詞'}
