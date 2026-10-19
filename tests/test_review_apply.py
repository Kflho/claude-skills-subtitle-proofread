"""Review apply: decisions hit the SRT, bad ones roll back per cue."""

import pytest

from lib.review import apply as apply_mod
from lib.review import pack as pack_mod

SRT = ("1\n00:00:05,000 --> 00:00:06,500\nおまえなにしてんだ\n\n"
       "2\n00:00:07,000 --> 00:00:08,000\nなんだと\n\n"
       "3\n00:00:09,000 --> 00:00:12,000\nはい\n\n")


@pytest.fixture
def srt(tmp_path):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    return path


def _item(item_id, cue_index, start, end, **kwargs):
    item = {
        'id': item_id, 'cue_index': cue_index, 'kind': 'asr_repair',
        'start': pack_mod._hms(start), 'end': pack_mod._hms(end),
        'seconds': {'start': start, 'end': end},
        'text': kwargs.pop('text', ''), 'decision': '',
        'new_start': None, 'new_end': None, 'replacement': '', 'why': '',
    }
    item.update(kwargs)
    return item


def _pack(srt_path, *items):
    return {'schema_version': 1, 'kind': 'review_pack', 'episode': 'EP001',
            'srt': str(srt_path), 'items': list(items)}


def test_replace_text_applied(srt):
    pack = _pack(srt, _item(0, 0, 5.0, 6.5, decision='replace_text',
                            replacement='お前何してんだよ'))

    result = apply_mod.apply_pack(pack)

    assert result['applied'] == 1
    text = srt.read_text(encoding='utf-8')
    assert 'お前何してんだよ' in text and 'おまえなにしてんだ' not in text
    assert pack['items'][0]['applied'] is True


def test_set_time_applied(srt):
    pack = _pack(srt, _item(0, 0, 5.0, 6.5, decision='set_time',
                            new_start=5.2, new_end=6.6))

    result = apply_mod.apply_pack(pack)

    assert result['applied'] == 1
    text = srt.read_text(encoding='utf-8')
    assert '00:00:05,200 --> 00:00:06,600' in text


def test_set_time_that_overlaps_neighbour_rolls_back(srt):
    pack = _pack(srt, _item(0, 0, 5.0, 6.5, decision='set_time',
                            new_start=5.0, new_end=9.0))

    result = apply_mod.apply_pack(pack)

    assert result['applied'] == 0
    assert result['rejected'] == 1
    assert 'overlaps' in pack['items'][0]['rejected']
    assert srt.read_text(encoding='utf-8') == SRT


def test_delete_line_applied_and_last_cue_protected(srt):
    pack = _pack(srt, _item(0, 1, 7.0, 8.0, decision='delete_line'))

    result = apply_mod.apply_pack(pack)
    assert result['applied'] == 1
    assert 'なんだと' not in srt.read_text(encoding='utf-8')

    solo = srt.parent / 'solo.srt'
    solo.write_text("1\n00:00:05,000 --> 00:00:06,000\nめ\n\n", encoding='utf-8')
    pack = _pack(solo, _item(0, 0, 5.0, 6.0, decision='delete_line'))
    result = apply_mod.apply_pack(pack)
    assert result['applied'] == 0
    assert 'last cue' in pack['items'][0]['rejected']


def test_undecided_and_keep_never_touch_the_file(srt):
    pack = _pack(srt,
                 _item(0, 0, 5.0, 6.5),
                 _item(1, 1, 7.0, 8.0, decision='keep'))

    result = apply_mod.apply_pack(pack)

    assert result['applied'] == 0 and result['kept'] == 2
    assert result['outcomes'][0]['reason'] == 'undecided'
    assert result['outcomes'][1]['reason'] == 'model said keep'
    assert srt.read_text(encoding='utf-8') == SRT


def test_incomplete_decision_is_rejected(srt):
    pack = _pack(srt, _item(0, 0, 5.0, 6.5, decision='replace_text',
                            replacement='   '))

    result = apply_mod.apply_pack(pack)

    assert result['rejected'] == 1
    assert 'incomplete' in pack['items'][0]['rejected']


def test_missing_or_non_srt_target(srt, tmp_path):
    assert apply_mod.apply_pack(_pack(tmp_path / 'nope.srt'))['error'] == 'missing srt'

    ass = tmp_path / 'EP002.ass'
    ass.write_text('', encoding='utf-8')
    assert apply_mod.apply_pack(_pack(ass))['error'].startswith('apply supports SRT')


def test_dry_run_keeps_file_untouched(srt):
    pack = _pack(srt, _item(0, 0, 5.0, 6.5, decision='replace_text',
                            replacement='お前何してんだよ'))

    result = apply_mod.apply_pack(pack, dry_run=True)

    assert result['applied'] == 1
    assert srt.read_text(encoding='utf-8') == SRT
