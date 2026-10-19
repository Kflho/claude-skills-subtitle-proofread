"""Review fill: routed gaps only, decisions validated before they can land."""

import json

import pytest

from lib.review import fill as fill_mod
from lib.review import pack as pack_mod


def _item(item_id, *, kind='asr_repair', text='おまえなにしてんだ',
          source='', asr='お前何してんだよ', start=5.0, end=6.5,
          similarity=0.9, route='text', failure=None, hint='review_ai'):
    return {
        'id': item_id, 'cue_index': item_id, 'kind': kind,
        'route': route, 'route_reason': 'test',
        'failure': failure or [],
        'start': pack_mod._hms(start), 'end': pack_mod._hms(end),
        'seconds': {'start': start, 'end': end},
        'text': text, 'source_text': source, 'asr_text': asr,
        'similarity': similarity,
        'length_ratio': round(len(asr) / max(1, len(text)), 2),
        'local_similarity': 0.7, 'window_shift': 0.0,
        'asr_window': {'text': asr, 'speech_start': start,
                       'speech_end': end, 'speech_seconds': end - start},
        'start_error_ms': None, 'end_error_ms': None,
        'glossary_misses': [], 'reasons': ['low_confidence'], 'hint': hint,
        'suggested': {'new_start': None, 'new_end': None, 'new_text': ''},
        'context': {'prev': [], 'next': []},
        'decision': '', 'new_start': None, 'new_end': None,
        'replacement': '', 'decided_by': '', 'why': '', 'rejected': '',
    }


def _pack(*items):
    return {'schema_version': 2, 'kind': 'review_pack', 'episode': 'EP001',
            'srt': 'EP001.srt', 'items': list(items)}


def _chat(payload, *, calls=None):
    def chat(messages):
        if calls is not None:
            calls.append(messages)
        return json.dumps(payload, ensure_ascii=False), ''

    chat.calls = calls if calls is not None else []
    return chat


def test_fill_applies_keep_replace_and_delete():
    pack = _pack(
        _item(0),
        _item(1, kind='translation', route='text', text='早く こっちへ！',
              source='早く こっちへ！', failure=['japanese_residual']),
        _item(2, route='delete', text='め', similarity=0.0),
    )
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'keep', 'why': '字幕卡'},
        {'id': 1, 'decision': 'replace_text', 'replacement': '快来这边！'},
        {'id': 2, 'decision': 'delete', 'why': '噪声'},
    ]})

    stats = fill_mod.fill_pack(
        pack, chat=chat,
        allowed_actions={'asr_repair': {'keep', 'rewrite', 'delete'},
                         'translation': {'keep', 'rewrite'}})

    assert stats['filled'] == 3 and stats['rejected'] == 0
    assert stats['sent'] == 3
    assert [i['decision'] for i in pack['items']] == [
        'keep', 'replace_text', 'delete_line']
    assert pack['items'][1]['replacement'] == '快来这边！'
    assert pack['stats']['decided'] == 3


def test_time_route_is_never_sent_to_the_model():
    """Timing belongs to the L0 planner; the model must not write times."""
    pack = _pack(_item(0, route='time'), _item(1, route='text'),
                 _item(2, route='keep'))
    assert [i['id'] for i in pack_mod.routable_items(pack)] == [1]

    calls = []
    fill_mod.fill_pack(pack, chat=_chat({'decisions': []}, calls=calls),
                       allowed_actions={'asr_repair': {'keep', 'rewrite'}})
    payload = json.loads(calls[0][1]['content'])
    assert [row['id'] for row in payload] == [1]
    assert pack['items'][0]['decision'] == ''


def test_model_cannot_move_times():
    pack = _pack(_item(0, route='text'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'set_time',
                                 'new_start': 5.4, 'new_end': 6.4}]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep', 'rewrite'}})

    assert stats['rejected'] == 1
    assert 'not allowed for route' in pack['items'][0]['rejected']


def test_only_routable_gaps_are_sent():
    pack = _pack(_item(0), _item(1, route='keep'), _item(2, route='time'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'keep'}]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep'}})

    assert stats['sent'] == 1
    assert stats['skipped_unroutable'] == 2


def test_fill_rejects_unsafe_decisions_and_falls_back_to_keep():
    pack = _pack(
        _item(0, route='text'),                        # latin-only replacement
        _item(1, kind='translation', route='text', text='おまえなにしてんだ',
              source='おまえなにしてんだ', failure=['japanese_residual']),
        _item(2, route='delete', text='これは本当に重要な台詞です',
              similarity=0.9),
    )
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'replace_text', 'replacement': 'what is it'},
        {'id': 1, 'decision': 'replace_text', 'replacement': 'おまえ何してる'},
        {'id': 2, 'decision': 'delete_line'},
    ]})

    stats = fill_mod.fill_pack(
        pack, chat=chat,
        allowed_actions={'asr_repair': {'keep', 'rewrite', 'delete'},
                         'translation': {'keep', 'rewrite'}})

    assert stats['rejected'] == 3
    for item in pack['items']:
        assert item['decision'] == 'keep'
        assert item['rejected']
    assert 'dropped Japanese' in pack['items'][0]['rejected']
    assert 'kana' in pack['items'][1]['rejected']
    assert 'real dialogue' in pack['items'][2]['rejected']


def test_action_outside_the_route_is_rejected():
    pack = _pack(_item(0, route='text'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'delete_line'}]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep', 'delete'}})

    assert stats['rejected'] == 1
    assert 'not allowed for route' in pack['items'][0]['rejected']


def test_short_translation_may_be_one_character():
    pack = _pack(_item(0, kind='translation', route='text', text='ウン',
                       source='ウン', failure=['japanese_residual']))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'replace_text',
                                 'replacement': '嗯'}]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'translation': {'keep', 'rewrite'}})

    assert stats['filled'] == 1


def test_fill_skips_items_that_already_have_a_decision():
    item = _item(0)
    item['decision'] = 'keep'
    pack = _pack(item)
    chat = _chat({'decisions': []})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep'}})

    assert stats['filled'] == 0
    assert chat.calls == []          # nothing pending → no model call


def test_fill_survives_failed_and_malformed_batches():
    pack = _pack(*[_item(i) for i in range(3)])

    def broken(messages):
        return '', 'network down'

    stats = fill_mod.fill_pack(pack, chat=broken, batch_size=2,
                               allowed_actions={'asr_repair': {'keep'}})
    assert stats['failed_batches'] == 2
    assert stats['filled'] == 0
    assert all(i['decision'] == '' for i in pack['items'])

    def prose(messages):
        return '抱歉，我无法回答。', ''

    stats = fill_mod.fill_pack(pack, chat=prose, batch_size=2,
                               allowed_actions={'asr_repair': {'keep'}})
    assert stats['failed_batches'] == 2


def test_missing_ids_are_retried_once():
    pack = _pack(_item(0), _item(1))
    calls = []

    def chat(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({'decisions': [{'id': 0, 'decision': 'keep'}]}), ''
        return json.dumps({'decisions': [{'id': 1, 'decision': 'keep'}]}), ''

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep'}})

    assert stats['retries'] == 1
    assert stats['filled'] == 2
    retry_payload = json.loads(calls[1][1]['content'])
    assert [row['id'] for row in retry_payload] == [1]


def test_duplicate_and_unknown_ids_are_counted():
    pack = _pack(_item(0))
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'keep'},
        {'id': 0, 'decision': 'keep'},
        {'id': 99, 'decision': 'keep'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'asr_repair': {'keep'}})

    assert stats['duplicate_id'] == 1
    assert stats['unknown_id'] == 1


def test_batches_respect_the_character_budget():
    items = [_item(i, text='あ' * 500) for i in range(10)]
    batches = fill_mod._make_batches(items, max_items=40,
                                     max_chars=fill_mod.MAX_BATCH_CHARS)
    assert len(batches) > 1
    for batch in batches:
        assert fill_mod._batch_cost(batch) <= fill_mod.MAX_BATCH_CHARS + 300


def test_prompt_payload_carries_kind_specific_fields():
    translation = _item(0, kind='translation', route='text', text='ウン',
                        source='ウン', failure=['japanese_residual'])
    translation['glossary_misses'] = ['アグモン']
    asr = _item(1)
    payload = json.loads(fill_mod._prompt_payload([translation, asr]))

    assert payload[0]['kind'] == 'translation'
    assert payload[0]['source_text'] == 'ウン'
    assert payload[0]['failure'] == ['japanese_residual']
    assert payload[0]['glossary_misses'] == ['アグモン']
    assert 'asr_text' not in payload[0]
    assert payload[1]['kind'] == 'asr_repair'
    assert payload[1]['asr_text'] == 'お前何してんだよ'
    assert 'source_text' not in payload[1]


def test_prompt_forbids_time_rewrites_and_requires_full_id_coverage():
    prompt = fill_mod.SYSTEM_PROMPT
    assert '不要输出任何时间字段' in prompt
    assert '每个 id 恰好出现一次' in prompt
    assert 'replacement' in prompt and 'glossary' in prompt.lower() or 'glossary' in prompt


def test_extract_json_object_handles_fences_and_prose():
    assert fill_mod._extract_json_object(
        '```json\n{"decisions": []}\n```') == {'decisions': []}
    assert fill_mod._extract_json_object(
        '先说明一下 {"decisions": [{"id": 1}]} 以上') == {'decisions': [{'id': 1}]}
    assert fill_mod._extract_json_object('no json here') is None


@pytest.mark.parametrize('value,expected', [
    ('keep', 'keep'), ('fix_time', 'set_time'), ('time', 'set_time'),
    ('replace', 'replace_text'), ('delete', 'delete_line'),
    ('', 'keep'), ('nonsense', ''),
])
def test_norm_decision_aliases(value, expected):
    assert fill_mod._norm_decision(value) == expected
