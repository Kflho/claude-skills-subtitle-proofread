"""Style patches: guard-railed rewrite proposals (no subtitle writes)."""

import json

import pytest

from lib.context.style_lint import lint_text
from lib.context.style_patch import (
    MAX_LENGTH_RATIO, MIN_LENGTH_RATIO, plan_patches, register_score,
    verify_patch,
)


def test_verify_accepts_clean_rewrite():
    before = '这事儿咋整啊，真离谱'
    after = '这事该怎么办，真是荒唐'
    ok, reason = verify_patch(before, after)
    assert ok, reason
    assert lint_text(after) == []


def test_verify_rejects_when_modern_words_remain():
    ok, reason = verify_patch('这事儿咋整啊', '这事儿咋办')
    assert not ok
    assert 'not reduced' in reason


def test_verify_rejects_literary_overshoot():
    before = '这些人不是盗贼吗'
    after = '此等人物，岂非盗贼乎'
    ok, reason = verify_patch(before, after)
    assert not ok
    assert 'literary' in reason


def test_verify_rejects_bad_length_ratio():
    ok, reason = verify_patch('我说啥了', '我说了些什么事情呢')
    assert not ok
    assert 'length ratio' in reason


def test_verify_rejects_lost_name():
    ok, reason = verify_patch('佐武，你咋来了', '他来了', names=['佐武'])
    assert not ok
    assert 'name lost' in reason


def test_verify_allows_name_preserved():
    ok, reason = verify_patch('佐武，你咋来了', '佐武，你怎么来了',
                              names=['佐武'])
    assert ok, reason


def test_register_score_is_bounded():
    assert register_score('') == 0.0
    assert register_score('此等人物岂非盗贼乎') > register_score('这是谁啊')


def _chat(rows):
    def chat(messages):
        return json.dumps(rows, ensure_ascii=False), ''
    return chat


def test_plan_patches_accepts_and_rejects():
    flagged = [
        {'episode': 'EP001', 'cue': 1, 'start': '00:00:01,000',
         'text': '这事儿咋整啊'},
        {'episode': 'EP001', 'cue': 2, 'start': '00:00:03,000',
         'text': '这些人不是盗贼吗'},
    ]
    chat = _chat([
        {'id': 0, 'text': '这事该怎么办'},
        {'id': 1, 'text': '此等人物，岂非盗贼乎'},
    ])
    result = plan_patches(flagged, chat=chat)

    assert result['asked'] == 2
    assert [p['cue'] for p in result['accepted']] == [1]
    assert result['accepted'][0]['after'] == '这事该怎么办'
    assert result['rejected'][0]['cue'] == 2
    assert 'literary' in result['rejected'][0]['reason']


def test_plan_patches_handles_missing_ids():
    flagged = [{'episode': 'EP001', 'cue': 1, 'text': '这事儿咋整啊'}]
    chat = _chat([{'id': 9, 'text': '不该用'}])
    result = plan_patches(flagged, chat=chat)
    assert result['accepted'] == []
    assert result['rejected'][0]['reason'] == 'no rewrite returned'


def test_plan_patches_empty_input():
    assert plan_patches([]) == {'accepted': [], 'rejected': [], 'asked': 0}


def test_plan_patches_survives_bad_json():
    def chat(messages):
        return '抱歉', ''
    result = plan_patches([{'episode': 'E', 'cue': 1, 'text': '咋'}], chat=chat)
    assert result['accepted'] == []
