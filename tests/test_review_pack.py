"""Review pack: routing, evidence, stats and round-trip (v2 artifact)."""

import json

import pytest

from lib.alignment.base import CueAlignment
from lib.asr.base import Segment
from lib.review import pack as pack_mod

CUES = [
    {'start': '00:00:05,000', 'end': '00:00:06,500',
     'start_s': 5.0, 'end_s': 6.5, 'text': 'おまえなにしてんだ'},
    {'start': '00:00:07,000', 'end': '00:00:08,000',
     'start_s': 7.0, 'end_s': 8.0, 'text': 'なんだと'},
    {'start': '00:00:09,000', 'end': '00:00:12,000',
     'start_s': 9.0, 'end_s': 12.0, 'text': 'はい'},
]


def _plan(index, *, action='review_ai', similarity=0.9, text=None, auto=False):
    from fix.align_repair import PlannedRepair
    cue = CUES[index]
    return PlannedRepair(
        cue_index=index, action=action, auto=auto,
        cue_start=cue['start_s'], cue_end=cue['end_s'],
        old_text=cue['text'] if text is None else text,
        confidence='low', similarity=similarity, reasons=['reason'])


def _alignment(index, *, start=None, end=None, similarity=0.9, asr_text=''):
    cue = CUES[index]
    return CueAlignment(
        cue_index=index, status='low', cue_start=cue['start_s'],
        cue_end=cue['end_s'], similarity=similarity, asr_text=asr_text,
        start=cue['start_s'] if start is None else start,
        end=cue['end_s'] if end is None else end)


def _segments():
    return [Segment('おまえなにしてんだよ', 5.2, 6.3),
            Segment('なんだと', 7.0, 7.8)]


def test_routes_gaps_by_the_evidence_they_carry():
    alignments = [
        _alignment(0, start=4.6, end=6.4, similarity=0.9,
                   asr_text='おまえなにしてんだよ'),          # time (shift .4s)
        _alignment(1, start=7.0, end=8.0, similarity=0.9,
                   asr_text='なんだと！'),                    # text (no shift)
        _alignment(2, start=None, end=None, similarity=0.1),  # delete candidate
    ]
    plan = [_plan(0), _plan(1), _plan(2, action='delete_candidate',
                                      similarity=0.1)]

    pack = pack_mod.build_pack('EP001', CUES, alignments, plan,
                               srt_path='EP001.srt', auto_count=4,
                               segments=_segments())

    routes = [i['route'] for i in pack['items']]
    assert routes == ['time', 'text', 'delete']
    assert pack['stats']['routed'] == 3
    assert pack['stats']['auto'] == 4
    assert pack['items'][0]['suggested']['new_start'] == 4.6
    assert pack['items'][1]['suggested']['new_text'] == 'なんだと！'
    assert pack['items'][2]['suggested']['new_text'] == ''


def test_unmatched_gap_is_not_routed_and_carries_no_fake_suggestion():
    alignments = [_alignment(0, start=92.0, end=93.0, similarity=0.05,
                             asr_text='おい')]
    plan = [_plan(0, action='replace_text', similarity=0.05)]

    pack = pack_mod.build_pack('EP001', CUES, alignments, plan,
                               srt_path='EP001.srt', segments=[])

    item = pack['items'][0]
    assert item['route'] == 'keep'
    assert item['suggested'] == {'new_start': None, 'new_end': None,
                                 'new_text': ''}
    assert item['local_similarity'] == 0.0
    assert pack['stats']['routed'] == 0


def test_window_speech_gives_a_time_proposal_without_a_text_match():
    """The cue window says the line starts 0.6 s later — enough to route."""
    segments = [Segment('まれにみるあらしだった', 5.6, 7.4)]
    unmatched = CueAlignment(cue_index=0, status='unmatched',
                             cue_start=5.0, cue_end=7.2, similarity=0.05)
    plan = [_plan(0, action='review_ai', similarity=0.05)]

    pack = pack_mod.build_pack('EP001',
                               [{'start': '00:00:05,000', 'end': '00:00:07,200',
                                 'start_s': 5.0, 'end_s': 7.2,
                                 'text': 'まれにみる嵐だった'}],
                               [unmatched], plan, segments=segments)

    item = pack['items'][0]
    assert item['route'] == 'time'
    assert item['window_shift'] == pytest.approx(0.6)
    assert item['suggested']['new_start'] == pytest.approx(5.6)
    assert item['suggested']['new_end'] == pytest.approx(7.4)


def test_asr_window_reports_local_speech_only():
    # window 5.0–6.5 is padded by ±1s, so the 7.0 s segment is included too
    window = pack_mod.asr_window(_segments(), 5.0, 6.5)
    assert window['text'].startswith('おまえなにしてんだよ')
    assert window['speech_start'] == pytest.approx(5.2)
    assert window['speech_end'] == pytest.approx(7.8)
    assert window['speech_seconds'] == pytest.approx(1.1 + 0.5)

    empty = pack_mod.asr_window(_segments(), 30.0, 31.0)
    assert empty == {'text': '', 'speech_seconds': 0.0,
                     'speech_start': None, 'speech_end': None}


def test_build_pack_keeps_context_and_seconds():
    alignments = [_alignment(1, start=7.0, end=8.0, similarity=0.9,
                             asr_text='なんだと')]
    pack = pack_mod.build_pack('EP002', CUES, alignments, [_plan(1)],
                               srt_path='EP002.srt', segments=_segments())

    item = pack['items'][0]
    assert item['seconds'] == {'start': 7.0, 'end': 8.0}
    assert item['start'] == '00:00:07,000'
    assert item['context']['prev'][0]['text'] == 'おまえなにしてんだ'
    assert item['context']['next'][0]['text'] == 'はい'
    assert item['length_ratio'] == pytest.approx(1.0)
    assert item['decision'] == '' and item['rejected'] == ''


def test_pack_roundtrip_pending_and_routable(tmp_path):
    alignments = [_alignment(0, start=4.6, similarity=0.9,
                             asr_text='おまえなにしてんだよ')]
    pack = pack_mod.build_pack('EP003', CUES, alignments, [_plan(0)],
                               segments=_segments())
    path = pack_mod.save_pack(pack_mod.pack_path(str(tmp_path), 'EP003'), pack)
    loaded = pack_mod.load_pack(path)

    assert pack_mod.pending_items(loaded) == loaded['items']
    assert len(pack_mod.routable_items(loaded)) == 1

    loaded['items'][0]['decision'] = 'keep'
    assert pack_mod.pending_items(loaded) == []
    assert pack_mod.stats(loaded)['kept'] == 1


def test_load_pack_rejects_foreign_json(tmp_path):
    path = tmp_path / 'x.ai_pack.json'
    path.write_text(json.dumps({'kind': 'something_else'}), encoding='utf-8')
    assert pack_mod.load_pack(str(path)) is None
    path.write_text('not json', encoding='utf-8')
    assert pack_mod.load_pack(str(path)) is None


def test_time_helpers_round_trip():
    assert pack_mod._hms(3725.5) == '01:02:05,500'
    assert pack_mod._seconds('01:02:05,500') == pytest.approx(3725.5)
    assert pack_mod._seconds(12.25) == pytest.approx(12.25)
    assert pack_mod._seconds('') is None
    assert pack_mod._seconds('nope') is None


def test_route_item_thresholds():
    route, _ = pack_mod.route_item(cue_text='あ' * 10, asr_text='あ' * 10,
                                   similarity=0.9, start_error=-0.5,
                                   hint='review_ai')
    assert route == 'time'

    route, _ = pack_mod.route_item(cue_text='あ' * 10, asr_text='あ' * 10,
                                   similarity=0.9, start_error=-3.0,
                                   hint='review_ai')
    assert route == 'keep'          # beyond max_time_shift → ambiguous match

    route, _ = pack_mod.route_item(cue_text='あ' * 10, asr_text='あ',
                                   similarity=0.9, start_error=0.0,
                                   hint='replace_text')
    assert route == 'keep'          # 1/10 chars: not a comparable sentence

    route, _ = pack_mod.route_item(cue_text='め', asr_text='',
                                   similarity=0.0, start_error=None,
                                   hint='delete_candidate')
    assert route == 'delete'
