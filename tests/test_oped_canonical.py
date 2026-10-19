"""Fill OP/ED candidate canonicals: meta first, AI for the rest, no auto-apply."""

import json

from lib.context.oped_canonical import (
    fill_candidates, match_meta, normalize, validate_canonical,
)
from nouns import context_review

META = {'fixed_lines': {'op_narration_zh': [
    '恨罪不恨人，斩人缚人皆为世人',
    '劈开黑暗的男人，行走于朦胧月夜，世间暗处',
]}}

CANDIDATES = [
    {'region': 'OP', 'episode_count': 30, 'time_position_s': 16.3,
     'variants': {'恨罪，但爱罪人，无论你是绑是杀，都是为了别人……': 5,
                  '恨罪不恨人，绑或杀，皆为人……': 2,
                  '恨罪，但爱罪人……': 3},
     'suggested_canonical': '恨罪，但爱罪人……',
     'suggested_confidence': 0.15, 'canonical': ''},
    {'region': 'OP', 'episode_count': 24, 'time_position_s': 35.9,
     'variants': {'佐武与市捕物控': 8, '佐武与市捕物控（佐武与市的逮捕令）': 2},
     'suggested_canonical': '佐武与市捕物控', 'canonical': ''},
]


def _chat(decisions):
    def chat(messages):
        return json.dumps({'decisions': decisions}, ensure_ascii=False), ''
    return chat


def test_normalize_strips_punctuation():
    assert normalize('恨罪，但爱罪人……') == '恨罪但爱罪人'


def test_meta_match_wins_for_reviewed_lines():
    canonical = match_meta(CANDIDATES[0], META)
    assert canonical == '恨罪不恨人，斩人缚人皆为世人'


def test_fill_uses_meta_then_llm():
    chat = _chat([{'id': 1, 'canonical': '佐武与市捕物控', 'reason': '标题'}])
    filled, stats = fill_candidates(
        [dict(c) for c in CANDIDATES], meta=META,
        profile_brief='brief', chat=chat)

    assert filled[0]['canonical'] == '恨罪不恨人，斩人缚人皆为世人'
    assert filled[0]['reviewed_by'] == 'meta'
    assert filled[1]['canonical'] == '佐武与市捕物控'
    assert filled[1]['reviewed_by'] == 'llm'
    assert stats == {'meta': 1, 'llm': 1, 'kept_empty': 0, 'rejected': 0}


def test_kana_canonical_is_rejected():
    chat = _chat([{'id': 0, 'canonical': '佐武と市'}])
    filled, stats = fill_candidates(
        [dict(CANDIDATES[1])], meta={}, profile_brief='b', chat=chat)
    assert filled[0]['canonical'] == ''
    assert stats['rejected'] == 1
    assert 'kana' in filled[0]['review_note']


def test_overlong_canonical_is_rejected():
    chat = _chat([{'id': 0, 'canonical': '很长的句子' * 30}])
    filled, stats = fill_candidates(
        [dict(CANDIDATES[1])], meta={}, profile_brief='b', chat=chat)
    assert stats['rejected'] == 1


def test_missing_decision_keeps_canonical_empty():
    filled, stats = fill_candidates(
        [dict(CANDIDATES[1])], meta={}, profile_brief='b',
        chat=_chat([]))
    assert filled[0]['canonical'] == ''
    assert stats['kept_empty'] == 1


def test_validate_canonical_rules():
    assert validate_canonical('佐武与市捕物控', ['佐武与市捕物控'])[0]
    ok, reason = validate_canonical('', ['x'])
    assert not ok and reason == 'empty'


def test_apply_text_keyed_unifies_by_text_not_time():
    from lib.context.oped_canonical import apply_text_keyed

    candidates = [{'canonical': '恨罪不恨人，斩人缚人皆为世人',
                   'variants': {'恨罪，但爱罪人……': 2,
                                '恨罪不恨人，绑或杀，皆为人……': 1}}]
    cues = {
        'EP001': [{'start': '00:00:16,300', 'text': '恨罪，但爱罪人……'}],
        'EP002': [{'start': '00:01:02,000', 'text': '恨罪，但爱罪人……'}],
        'EP003': [{'start': '00:00:40,000', 'text': '别的话'}],
    }
    new_cues, changes = apply_text_keyed(cues, candidates)

    assert len(changes) == 2
    assert new_cues['EP002'][0]['text'] == '恨罪不恨人，斩人缚人皆为世人'
    assert new_cues['EP003'][0]['text'] == '别的话'
    assert cues['EP001'][0]['text'] == '恨罪，但爱罪人……'   # input untouched


def test_apply_text_keyed_skips_candidates_without_canonical():
    from lib.context.oped_canonical import apply_text_keyed
    new_cues, changes = apply_text_keyed(
        {'EP001': [{'text': '恨罪，但爱罪人……'}]},
        [{'canonical': '', 'variants': {'恨罪，但爱罪人……': 1}}])
    assert changes == []


def test_cli_oped_writes_filled_report_without_touching_srt(tmp_path, monkeypatch):
    candidates = tmp_path / 'oped_ai_review.json'
    candidates.write_text(json.dumps({'candidates': [dict(CANDIDATES[1])]},
                                     ensure_ascii=False), encoding='utf-8')
    glossary = tmp_path / 'g.json'
    glossary.write_text(json.dumps({'サブ': '佐武'}, ensure_ascii=False),
                        encoding='utf-8')
    meta = tmp_path / 'm.json'
    meta.write_text(json.dumps({}, ensure_ascii=False), encoding='utf-8')
    srt = tmp_path / 'translated' / 'zh'
    srt.mkdir(parents=True)
    (srt / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n佐武与市捕物控\n\n", encoding='utf-8')
    before = (srt / 'EP001.srt').read_text(encoding='utf-8')

    def fake_chat(messages, **kwargs):
        return json.dumps({'decisions': [
            {'id': 0, 'canonical': '佐武与市捕物控', 'reason': '标题'}]},
            ensure_ascii=False), ''

    monkeypatch.setattr('lib.llm.call_chat', fake_chat)
    report = tmp_path / 'filled.json'
    rc = context_review.main([
        '--project', str(tmp_path), '--glossary', str(glossary),
        '--meta', str(meta), '--candidates', str(candidates),
        '--mode', 'oped', '--report', str(report)])
    assert rc == 0
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['candidates'][0]['canonical'] == '佐武与市捕物控'
    assert (srt / 'EP001.srt').read_text(encoding='utf-8') == before

def test_cli_text_keyed_in_place_backs_up(tmp_path, monkeypatch):
    candidates = tmp_path / 'cands.json'
    candidates.write_text(json.dumps({'candidates': [
        {'region': 'OP', 'episode_count': 3, 'variants': {'旧译法': 2},
         'canonical': ''}]}, ensure_ascii=False), encoding='utf-8')
    glossary = tmp_path / 'g.json'
    glossary.write_text(json.dumps({'サブ': '佐武'}, ensure_ascii=False),
                        encoding='utf-8')
    meta = tmp_path / 'm.json'
    meta.write_text('{}', encoding='utf-8')
    zh = tmp_path / 'translated' / 'zh'
    zh.mkdir(parents=True)
    (zh / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n旧译法\n\n", encoding='utf-8')

    def fake_chat(messages, **kwargs):
        return json.dumps({'decisions': [{'id': 0, 'canonical': '规范句'}]},
                          ensure_ascii=False), ''

    monkeypatch.setattr('lib.llm.call_chat', fake_chat)
    rc = context_review.main([
        '--project', str(tmp_path), '--glossary', str(glossary),
        '--meta', str(meta), '--candidates', str(candidates), '--mode', 'oped',
        '--apply-text-keyed', '--in-place',
        '--report', str(tmp_path / 'r.json')])
    assert rc == 0
    assert '规范句' in (zh / 'EP001.srt').read_text(encoding='utf-8')
    assert '旧译法' in (zh / 'EP001.srt.bak').read_text(encoding='utf-8')
