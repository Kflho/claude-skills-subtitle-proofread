"""Tests for nouns/extractor.py — alignment, entity parsing, sidecar health."""

import json
import sys

from nouns import extractor as ex


def cue(text, start='00:00:00,000'):
    return {'text': text, 'start': start, 'end': start}


def ent(ja=(), zh=(), count=1, samples=()):
    return {'ja_forms': list(ja), 'zh_forms': list(zh), 'count': count,
            'samples': list(samples)}


# ═══════════════════════════════════════════════════════════════
# Timestamps / alignment
# ═══════════════════════════════════════════════════════════════

def test_time_to_seconds():
    assert ex._time_to_seconds('00:00:01,500') == 1.5
    assert ex._time_to_seconds('00:00:03.250') == 3.25
    assert ex._time_to_seconds('01:02:03,000') == 3723.0
    assert ex._time_to_seconds('bad') is None
    assert ex._time_to_seconds('') is None
    assert ex._time_to_seconds(None) is None


def test_align_pairs_equal_counts_uses_index():
    pairs = ex._align_pairs(
        [cue('こんにちは'), cue(''), cue('さようなら')],
        [cue('你好'), cue('空的'), cue('  ')])
    assert pairs == [('こんにちは', '你好')]


def test_align_pairs_unequal_counts_uses_nearest_timestamp(capsys):
    ja = [cue('一', '00:00:00,000'), cue('二', '00:00:01,000'),
          cue('三', '00:00:02,000')]
    zh = [cue('甲', '00:00:00,200'), cue('丙', '00:00:02,300'),
          cue('孤', '00:00:20,000'), cue('无时间', None)]

    pairs = ex._align_pairs(ja, zh)

    assert pairs == [('一', '甲'), ('三', '丙')]
    assert '时间戳对齐' in capsys.readouterr().err


def test_render_pairs():
    assert ex._render_pairs([('日', '中'), ('日2', '中2')]) == (
        '1. 日 | 中\n2. 日2 | 中2')


def test_normalize_forms():
    assert ex._normalize_forms([' アトム ', 'アトム', '', None, 'アットム']) == [
        'アトム', 'アットム']
    assert ex._normalize_forms(None) == []


# ═══════════════════════════════════════════════════════════════
# LLM response parsing / merging
# ═══════════════════════════════════════════════════════════════

def test_extract_entities_from_response_normalizes_fields():
    response = json.dumps([
        {'ja_forms': ['アトム', 'アトム', ' '], 'zh_forms': ['阿童木'],
         'count': '3', 'sample': 'アトム|阿童木'},
        {'ja_forms': [], 'zh_forms': []},                 # no forms → skip
        'not a dict',                                     # skip
        {'ja_forms': ['サリー'], 'zh_forms': ['莎莉'],
         'count': 'oops', 'samples': ['样例']},
    ], ensure_ascii=False)

    ents = ex._extract_entities_from_response(response)

    assert ents == [
        {'ja_forms': ['アトム'], 'zh_forms': ['阿童木'], 'count': 3,
         'samples': ['アトム|阿童木']},
        {'ja_forms': ['サリー'], 'zh_forms': ['莎莉'], 'count': 0,
         'samples': ['样例']},
    ]


def test_extract_entities_from_response_rejects_garbage(capsys):
    assert ex._extract_entities_from_response('完全不是 JSON') == []


def test_merge_entities_by_shared_forms():
    ents = [
        ent(ja=['アトム', 'アットム'], zh=['阿童木'], count=2,
            samples=['s1']),
        ent(ja=['アトム'], zh=['阿托姆'], count=3, samples=['s2']),
        ent(ja=['サリー'], zh=['莎莉'], count=1, samples=['s3']),
    ]

    merged = ex._merge_entities(ents)

    assert len(merged) == 2
    atom = merged[0]
    assert atom['count'] == 5
    assert atom['ja_forms'] == ['アットム', 'アトム']   # longest first
    assert set(atom['zh_forms']) == {'阿托姆', '阿童木'}
    assert atom['samples'] == ['s1', 's2']


def test_merge_entities_caps_samples_at_two():
    ents = [ent(ja=['A'], count=1, samples=[f's{i}']) for i in range(4)]
    merged = ex._merge_entities(ents)
    assert merged[0]['samples'] == ['s0', 's1']


# ═══════════════════════════════════════════════════════════════
# extract_names orchestration
# ═══════════════════════════════════════════════════════════════

def test_extract_names_chunks_and_reports_stats(monkeypatch):
    calls = []

    def fake_chunk(pairs, api_key, model, base_url, chunk_no, chunk_total):
        calls.append((chunk_no, chunk_total, len(pairs)))
        if chunk_no == 2:
            return [], False        # failed chunk
        return [ent(ja=[f'E{chunk_no}'], zh=[f'实{chunk_no}'])], True

    monkeypatch.setattr(ex, '_extract_chunk', fake_chunk)
    ja = [cue(f'日{i}') for i in range(3)]
    zh = [cue(f'中{i}') for i in range(3)]
    stats = {}

    entities = ex.extract_names(ja, zh, chunk_size=2, quiet=True, stats=stats)

    assert calls == [(1, 2, 2), (2, 2, 1)]
    assert stats == {'chunks_total': 2, 'chunks_failed': 1}
    assert [e['ja_forms'] for e in entities] == [['E1']]


def test_extract_names_without_pairs(monkeypatch):
    stats = {}
    assert ex.extract_names([], [], stats=stats) == []
    assert stats == {'chunks_total': 0, 'chunks_failed': 0}


# ═══════════════════════════════════════════════════════════════
# Episode file resolution
# ═══════════════════════════════════════════════════════════════

def test_resolve_episode_file_exact_and_dvd_styles(tmp_path):
    (tmp_path / 'EP001.srt').write_text('', encoding='utf-8')
    (tmp_path / 'Show - 002v2 - 1080p.srt').write_text('', encoding='utf-8')
    (tmp_path / 'notes.txt').write_text('', encoding='utf-8')

    exact = ex.resolve_episode_file(str(tmp_path), 'EP001')
    dvd = ex.resolve_episode_file(str(tmp_path), '002')

    assert exact.endswith('EP001.srt')
    assert dvd.endswith('Show - 002v2 - 1080p.srt')
    assert ex.resolve_episode_file(str(tmp_path), '999') is None
    assert ex.resolve_episode_file(str(tmp_path / 'missing'), '001') is None


def test_resolve_episode_pair(tmp_path):
    ja_dir = tmp_path / 'ja'
    zh_dir = tmp_path / 'zh'
    ja_dir.mkdir()
    zh_dir.mkdir()
    (ja_dir / 'EP003.srt').write_text('', encoding='utf-8')
    (zh_dir / 'EP003.srt').write_text('', encoding='utf-8')

    ja_path, zh_path = ex.resolve_episode_pair(str(ja_dir), str(zh_dir),
                                               'EP003')
    assert ja_path.endswith('EP003.srt') and zh_path.endswith('EP003.srt')

    assert ex.resolve_episode_pair(str(ja_dir), str(zh_dir), 'EP009') == (
        None, None)


# ═══════════════════════════════════════════════════════════════
# extract_episode + sidecar health
# ═══════════════════════════════════════════════════════════════

def test_extract_episode_writes_sidecar(monkeypatch, tmp_path):
    ja_dir = tmp_path / 'ja'
    zh_dir = tmp_path / 'zh'
    out_dir = tmp_path / 'nouns'
    ja_dir.mkdir()
    zh_dir.mkdir()
    (ja_dir / 'EP004.srt').write_text('', encoding='utf-8')
    (zh_dir / 'EP004.srt').write_text('', encoding='utf-8')

    monkeypatch.setattr(ex, 'parse_subtitles',
                        lambda path, **kwargs: [cue('日')])

    def fake_extract_names(ja, zh, **kwargs):
        kwargs['stats'].update({'chunks_total': 1, 'chunks_failed': 0})
        return [ent(ja=['アトム'], zh=['阿童木'])]

    monkeypatch.setattr(ex, 'extract_names', fake_extract_names)

    ep_id, entities = ex.extract_episode(str(ja_dir), str(zh_dir), '004',
                                         output_dir=str(out_dir))

    assert ep_id == 'EP004'
    assert entities[0]['ja_forms'] == ['アトム']
    sidecar = json.loads((out_dir / 'extracted_EP004.json').read_text(
        encoding='utf-8'))
    assert sidecar['episode'] == 'EP004'
    assert sidecar['chunks'] == {'chunks_total': 1, 'chunks_failed': 0}
    assert sidecar['entities'][0]['zh_forms'] == ['阿童木']


def test_extract_episode_without_matching_files(tmp_path, capsys):
    ja_dir = tmp_path / 'ja'
    zh_dir = tmp_path / 'zh'
    ja_dir.mkdir()
    zh_dir.mkdir()

    assert ex.extract_episode(str(ja_dir), str(zh_dir), '001') == (None, [])
    assert '无法匹配' in capsys.readouterr().err


def test_sidecar_health(tmp_path):
    missing = tmp_path / 'none.json'
    assert ex._sidecar_health(str(missing)) is None

    healthy = tmp_path / 'healthy.json'
    healthy.write_text(json.dumps(
        {'chunks': {'chunks_total': 2, 'chunks_failed': 0}, 'entities': []}),
        encoding='utf-8')
    assert ex._sidecar_health(str(healthy)) == 'skip'

    failed = tmp_path / 'failed.json'
    failed.write_text(json.dumps(
        {'chunks': {'chunks_total': 2, 'chunks_failed': 1}}),
        encoding='utf-8')
    assert ex._sidecar_health(str(failed)) == 'failed'

    corrupt = tmp_path / 'corrupt.json'
    corrupt.write_text('not json', encoding='utf-8')
    assert ex._sidecar_health(str(corrupt)) == 'failed'

    legacy_few = tmp_path / 'legacy_few.json'
    legacy_few.write_text(json.dumps({'entities': [{}, {}]}), encoding='utf-8')
    assert ex._sidecar_health(str(legacy_few), min_entities=2) == 'failed'

    legacy_ok = tmp_path / 'legacy_ok.json'
    legacy_ok.write_text(json.dumps({'entities': [{}, {}, {}]}),
                         encoding='utf-8')
    assert ex._sidecar_health(str(legacy_ok), min_entities=2) == 'skip'


# ═══════════════════════════════════════════════════════════════
# Episode spec parsing / status CLI
# ═══════════════════════════════════════════════════════════════

def test_parse_episodes():
    assert ex._parse_episodes('EP001-EP003') == ['EP001', 'EP002', 'EP003']
    assert ex._parse_episodes('1,3,3') == ['EP001', 'EP003']
    assert ex._parse_episodes(None) is None


def test_main_status_reports_health(monkeypatch, tmp_path, capsys):
    out_dir = tmp_path / 'nouns'
    out_dir.mkdir()
    (out_dir / 'extracted_EP001.json').write_text(json.dumps(
        {'chunks': {'chunks_total': 1, 'chunks_failed': 0}}), encoding='utf-8')
    (out_dir / 'extracted_EP002.json').write_text(json.dumps(
        {'chunks': {'chunks_total': 1, 'chunks_failed': 1}}), encoding='utf-8')
    ja_dir = tmp_path / 'ja'
    zh_dir = tmp_path / 'zh'
    ja_dir.mkdir()
    zh_dir.mkdir()

    monkeypatch.setattr(sys, 'argv', [
        'extractor.py', '--ja-dir', str(ja_dir), '--zh-dir', str(zh_dir),
        '--episodes', 'EP001-EP003', '-o', str(out_dir), '--status'])

    ex.main()

    err = capsys.readouterr().err
    assert '--status：共 3 集' in err
    assert '已提取健康：1' in err
    assert '失败/过少：1' in err
    assert '未提取：1' in err
