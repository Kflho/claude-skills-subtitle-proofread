"""Review fill: routed gaps only, decisions validated before they can land."""

import json

import pytest

from lib.review import fill as fill_mod
from lib.review import pack as pack_mod


def _item(item_id, *, text='おまえなにしてんだ', asr='お前何してんだよ',
          start=5.0, end=6.5, similarity=0.9, route='time',
          suggested=None, hint='review_ai'):
    return {
        'id': item_id, 'cue_index': item_id, 'kind': 'asr_repair',
        'route': route, 'route_reason': 'test',
        'start': pack_mod._hms(start), 'end': pack_mod._hms(end),
        'seconds': {'start': start, 'end': end},
        'text': text, 'asr_text': asr, 'similarity': similarity,
        'length_ratio': round(len(asr) / max(1, len(text)), 2),
        'asr_window': {'text': asr, 'speech_start': start,
                       'speech_end': end, 'speech_seconds': end - start},
        'start_error_ms': -300.0, 'end_error_ms': 100.0,
        'reasons': ['low_confidence'], 'hint': hint,
        'suggested': suggested or {'new_start': 5.25, 'new_end': 6.6,
                                   'new_text': asr if route == 'text' else ''},
        'context': {'prev': [], 'next': []},
        'decision': '', 'new_start': None, 'new_end': None,
        'replacement': '', 'decided_by': '', 'why': '', 'rejected': '',
    }


def _pack(*items):
    return {'schema_version': 2, 'kind': 'review_pack', 'episode': 'EP001',
            'srt': 'EP001.srt', 'items': list(items)}


def _chat(payload):
    calls = []

    def chat(messages):
        calls.append(messages)
        return json.dumps(payload, ensure_ascii=False), ''

    chat.calls = calls
    return chat


def test_fill_applies_each_decision_kind():
    pack = _pack(_item(0, route='time'),
                 _item(1, route='time'),
                 _item(2, route='text'),
                 _item(3, text='め', route='delete', similarity=0.05))
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'keep', 'why': '字幕卡'},
        {'id': 1, 'decision': 'fix_time', 'new_start': 9.4, 'new_end': 9.9},
        {'id': 2, 'decision': 'replace_text', 'replacement': 'お前何してんだよ'},
        {'id': 3, 'decision': 'delete', 'why': '噪声'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['filled'] == 4 and stats['rejected'] == 0
    assert stats['sent'] == 4
    assert stats['batches'] == 1
    assert [i['decision'] for i in pack['items']] == [
        'keep', 'set_time', 'replace_text', 'delete_line']
    assert pack['items'][1]['new_start'] == 9.4
    assert pack['items'][2]['replacement'] == 'お前何してんだよ'
    assert pack['stats']['decided'] == 4
    assert pack['stats']['set_time'] == 1


def test_only_routable_gaps_are_sent():
    pack = _pack(_item(0, route='time'),
                 _item(1, route='keep'),
                 _item(2, route='keep'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'keep'}]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['sent'] == 1
    assert stats['skipped_unroutable'] == 2
    payload = json.loads(chat.calls[0][1]['content'])
    assert [row['id'] for row in payload] == [0]
    assert [i['decision'] for i in pack['items']] == ['keep', '', '']


def test_fill_rejects_unsafe_decisions_and_falls_back_to_keep():
    pack = _pack(
        _item(0, route='time'),                                  # shift 20s
        _item(1, route='text'),                                  # latin-only
        _item(2, route='delete', text='これは本当に重要な台詞です',
              similarity=0.9),                                   # real line
    )
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'set_time', 'new_start': 25.0, 'new_end': 26.0},
        {'id': 1, 'decision': 'replace_text', 'replacement': 'what is it'},
        {'id': 2, 'decision': 'delete_line'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['filled'] == 0 and stats['rejected'] == 3
    for item in pack['items']:
        assert item['decision'] == 'keep'
        assert item['rejected']
    assert 'too large' in pack['items'][0]['rejected']
    assert 'dropped Japanese' in pack['items'][1]['rejected']
    assert 'real dialogue' in pack['items'][2]['rejected']


def test_triage_only_mode_rejects_rewrites_and_deletes():
    """REVIEW_ACTIONS=keep: the model may classify, not touch the file."""
    pack = _pack(_item(0, route='time'),
                 _item(1, route='delete', text='め', similarity=0.0))
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'set_time', 'new_start': 5.4, 'new_end': 6.4},
        {'id': 1, 'decision': 'delete_line'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat, allowed_actions={'keep'})

    assert stats['rejected'] == 2
    assert [i['decision'] for i in pack['items']] == ['keep', 'keep']
    assert 'REVIEW_ACTIONS' in pack['items'][0]['rejected']
    assert 'REVIEW_ACTIONS' in pack['items'][1]['rejected']


def test_delete_needs_the_delete_right():
    pack = _pack(_item(0, route='delete', text='め', similarity=0.0))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'delete_line'}]})

    stats = fill_mod.fill_pack(pack, chat=chat,
                               allowed_actions={'keep', 'rewrite'})

    assert stats['rejected'] == 1
    assert pack['items'][0]['decision'] == 'keep'


def test_action_outside_the_route_is_rejected():
    pack = _pack(_item(0, route='text'))
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'set_time', 'new_start': 5.4, 'new_end': 6.4},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['rejected'] == 1
    assert 'not allowed for route' in pack['items'][0]['rejected']
    assert pack['items'][0]['decision'] == 'keep'


def test_short_replacement_is_rejected():
    pack = _pack(_item(0, route='text', text='おまえなにしてんだよ'))
    chat = _chat({'decisions': [
        {'id': 0, 'decision': 'replace_text', 'replacement': 'おい'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['rejected'] == 1
    assert 'much shorter' in pack['items'][0]['rejected']


def test_fill_uses_suggested_times_when_model_omits_them():
    pack = _pack(_item(0, route='time'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'set_time'}]})

    fill_mod.fill_pack(pack, chat=chat)

    item = pack['items'][0]
    assert item['decision'] == 'set_time'
    assert (item['new_start'], item['new_end']) == (5.25, 6.6)


def test_fill_falls_back_to_speech_window_when_no_suggestion():
    item = _item(0, route='time', suggested={'new_start': None,
                                             'new_end': None, 'new_text': ''})
    item['asr_window'] = {'text': 'x', 'speech_start': 5.1,
                          'speech_end': 6.2, 'speech_seconds': 1.1}
    pack = _pack(item)
    chat = _chat({'decisions': [{'id': 0, 'decision': 'set_time'}]})

    fill_mod.fill_pack(pack, chat=chat)

    assert (pack['items'][0]['new_start'], pack['items'][0]['new_end']) == (5.1, 6.2)


def test_fill_skips_items_that_already_have_a_decision():
    item = _item(0, route='time')
    item['decision'] = 'keep'
    pack = _pack(item)
    chat = _chat({'decisions': []})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['filled'] == 0
    assert chat.calls == []          # nothing pending → no model call
    assert pack['items'][0]['decision'] == 'keep'


def test_fill_survives_failed_and_malformed_batches():
    pack = _pack(*[_item(i, route='time') for i in range(3)])

    def broken(messages):
        return '', 'network down'

    stats = fill_mod.fill_pack(pack, chat=broken, batch_size=2)
    assert stats['failed_batches'] == 2
    assert stats['filled'] == 0
    assert all(i['decision'] == '' for i in pack['items'])

    def prose(messages):
        return '抱歉，我无法回答。', ''

    stats = fill_mod.fill_pack(pack, chat=prose, batch_size=2)
    assert stats['failed_batches'] == 2


def test_fill_ignores_unknown_ids_and_unparseable_decisions():
    pack = _pack(_item(0, route='time'))
    chat = _chat({'decisions': [
        {'id': 99, 'decision': 'keep'},
        {'id': 0, 'decision': 'rewrite_everything'},
    ]})

    stats = fill_mod.fill_pack(pack, chat=chat)

    assert stats['unknown_id'] == 1
    assert stats['rejected'] == 1
    assert pack['items'][0]['decision'] == 'keep'


def test_prompt_payload_is_compact_and_carries_evidence():
    item = _item(0, route='time')
    item['text'] = 'あ' * 400
    payload = json.loads(fill_mod._prompt_payload([item]))

    assert payload[0]['id'] == 0
    assert payload[0]['route'] == 'time'
    assert payload[0]['asr_text'] == 'お前何してんだよ'
    assert payload[0]['start_err_ms'] == -300.0
    assert payload[0]['asr_window']['speech_start'] == 5.0
    assert payload[0]['text'].endswith('…')
    assert len(payload[0]['text']) <= fill_mod.MAX_TEXT_CHARS + 1


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
