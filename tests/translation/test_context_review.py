"""Context review: series profile + near-term/style agents (no subtitle writes)."""

import json
import os

import pytest

from lib.context.profile import build_profile, load_or_build, profile_brief
from lib.review.context_agent import (
    apply_approvals, review_near_terms, review_style,
)

GLOSSARY = {'サブ': '佐武', 'イチ': '市', 'サブトイチ': '佐武与市'}
META = {
    'series': {'era': '江户时代', 'genre': '时代剧 / 捕物帐',
               'cast': {'佐武': {'role': '浪人捕快'},
                        '市': {'role': '盲人按摩师'}}},
    'register': {'narration': '半文半白', 'dialogue': '口语',
                 'forbidden': ['现代网络语']},
}


def _profile(tmp_path=None):
    return build_profile(series='佐武与市捕物控', glossary=GLOSSARY,
                         meta=META, project_dir=str(tmp_path or ''))


def test_profile_contains_cast_and_era():
    profile = _profile()
    assert profile['era'] == '江户时代'
    assert profile['genre'] == '时代剧 / 捕物帐'
    zh = {c['zh'] for c in profile['cast']}
    assert {'佐武', '市'} <= zh
    brief = profile_brief(profile)
    assert '佐武与市捕物控' in brief and '人物' in brief and '语体' in brief


def test_profile_is_deterministic():
    assert _profile()['source_hash'] == _profile()['source_hash']


def test_summary_is_cached(tmp_path):
    calls = []

    def chat(messages):
        calls.append(messages)
        return '江户时代捕物帐故事，主角佐武与市。', ''

    path = tmp_path / 'profile.json'
    first = load_or_build(str(path), series='S', glossary=GLOSSARY,
                          meta=META, chat=chat)
    assert first['summary'].startswith('江户时代')
    second = load_or_build(str(path), series='S', glossary=GLOSSARY,
                           meta=META, chat=chat)
    assert second['summary'] == first['summary']
    assert len(calls) == 1          # cached, no second LLM call


def test_summary_failure_keeps_profile_usable(tmp_path):
    def broken(messages):
        return '', 'network down'

    profile = load_or_build(str(tmp_path / 'p.json'), series='S',
                            glossary=GLOSSARY, meta=META, chat=broken)
    assert profile['summary'] == ''
    assert '人物' in profile_brief(profile)


def _chat(decisions):
    def chat(messages):
        return json.dumps({'decisions': decisions}, ensure_ascii=False), ''
    return chat


def test_review_near_terms_maps_and_validates():
    candidates = [
        {'candidate': '沙布', 'canonical': '佐武', 'positive': 12,
         'negative': 0, 'examples': [{'ja': 'サブ', 'zh': '沙布'}]},
        {'candidate': '劈开黑暗', 'canonical': '佐武与市', 'positive': 20,
         'negative': 4, 'examples': []},
    ]
    chat = _chat([
        {'id': 0, 'verdict': 'promote', 'canonical': '佐武', 'reason': '音译变体'},
        {'id': 1, 'verdict': 'reject', 'reason': '这是旁白句子，不是人名'},
        {'id': 99, 'verdict': 'promote', 'reason': 'unknown id'},
        {'id': 0, 'verdict': 'maybe', 'reason': 'bad verdict'},
    ])
    decisions = review_near_terms(candidates, 'brief', chat=chat)

    assert [d['candidate'] for d in decisions] == ['沙布', '劈开黑暗']
    assert decisions[0]['verdict'] == 'promote'
    assert decisions[1]['verdict'] == 'reject'
    assert all(d['verdict'] in ('promote', 'reject', 'uncertain') for d in decisions)


def test_review_near_terms_survives_bad_json():
    def chat(messages):
        return '抱歉，我无法回答。', ''
    assert review_near_terms([{'candidate': 'x'}], 'brief', chat=chat) == []


def test_review_style_filters_unknown_issues():
    items = [{'episode': 'EP001', 'cue': 1, 'text': '你这家伙真是离谱'},
             {'episode': 'EP001', 'cue': 2, 'text': '恨罪不恨人，斩人缚人皆为世人'}]
    chat = _chat([
        {'id': 0, 'issues': ['现代语'], 'severity': 3, 'suggestion': '改「你这厮」'},
        {'id': 1, 'issues': [], 'severity': 1},
        {'id': 1, 'issues': ['不是问题'], 'severity': 2},
    ])
    flags = review_style(items, 'brief', chat=chat)

    assert len(flags) == 1
    assert flags[0]['issues'] == ['现代语']
    assert flags[0]['severity'] == 3


def test_apply_approvals_updates_meta_with_backup(tmp_path):
    meta_path = tmp_path / 'meta.json'
    meta_path.write_text(json.dumps({'aliases': {'佐武': ['サブヤン']}},
                                    ensure_ascii=False), encoding='utf-8')
    decisions = [
        {'candidate': '沙布', 'canonical_final': '佐武', 'verdict': 'promote'},
        {'candidate': '劈开黑暗', 'canonical_final': '佐武与市', 'verdict': 'reject'},
        {'candidate': '幽灵', 'canonical_final': '不存在', 'verdict': 'promote'},
    ]
    result = apply_approvals(str(meta_path), decisions, GLOSSARY)

    assert result['applied'] == [{'candidate': '沙布', 'canonical': '佐武'}]
    assert any(s['reason'] == 'canonical not in glossary'
               for s in result['skipped'])
    saved = json.loads(meta_path.read_text(encoding='utf-8'))
    assert '沙布' in saved['aliases']['佐武']
    assert (tmp_path / 'meta.json.bak').exists()


def test_apply_approvals_is_idempotent(tmp_path):
    meta_path = tmp_path / 'meta.json'
    meta_path.write_text(json.dumps({'aliases': {'佐武': ['沙布']}},
                                    ensure_ascii=False), encoding='utf-8')
    result = apply_approvals(str(meta_path), [
        {'candidate': '沙布', 'canonical_final': '佐武', 'verdict': 'promote'}],
        GLOSSARY)
    assert result['applied'] == []
    assert result['skipped'][0]['reason'] == 'already known'
