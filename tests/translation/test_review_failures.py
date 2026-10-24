"""Detector-flagged failures: QA → review pack → translation fix."""

import json

import pytest

from lib.review import failures as review_failures
from lib.review import fill as review_fill
from lib.review import pack as pack_mod

SRT = ("1\n00:00:05,000 --> 00:00:06,500\nおまえなにしてんだ\n\n"
       "2\n00:00:07,000 --> 00:00:08,000\nなんだと\n\n"
       "3\n00:00:09,000 --> 00:00:12,000\nはい\n\n")


def _cues():
    from lib.subtitle_io import read_subtitles
    import tempfile, os
    path = os.path.join(tempfile.mkdtemp(), 'x.srt')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(SRT)
    return read_subtitles(path, mark_garbled=False)


def test_items_from_qa_build_translation_items():
    cues = _cues()
    qa = {'items': [{'index': 0, 'reasons': ['japanese_residual', 'echo'],
                     'source': 'おまえなにしてんだ', 'target': 'おまえなにしてんだ'},
                    {'index': 1, 'reasons': ['timing'], 'source': 'なんだと',
                     'target': '什么'}]}

    items = review_failures.items_from_qa(qa, cues)

    assert [i['cue_index'] for i in items] == [0, 1]
    first = items[0]
    assert first['kind'] == 'translation'
    assert first['route'] == 'text'
    assert first['failure'] == ['japanese_residual', 'echo']
    assert first['source_text'] == 'おまえなにしてんだ'
    assert first['text'] == 'おまえなにしてんだ'
    assert first['start'] == '00:00:05,000'
    assert first['context']['next'][0]['text'] == 'なんだと'
    assert items[1]['route'] == 'keep'          # timing is not a text fix


def test_classify_reasons():
    assert review_failures.classify_reasons(
        ['japanese_residual', 'timing'])[0] == 'text'
    assert review_failures.classify_reasons(['timing']) == ('keep', ['timing'])


def test_attach_to_packs_writes_zh_pack_and_keeps_decisions(tmp_path):
    zh_dir = tmp_path / 'translated' / 'zh'
    zh_dir.mkdir(parents=True)
    (zh_dir / 'EP001.srt').write_text(SRT, encoding='utf-8')
    (zh_dir / 'EP001.qa.json').write_text(json.dumps({
        'items': [{'index': 0, 'reasons': ['japanese_residual'],
                   'source': 'おまえなにしてんだ', 'target': 'おまえなにしてんだ'}]},
        ensure_ascii=False), encoding='utf-8')

    scan_dir = tmp_path / 'temp' / 'scans'
    written = review_failures.attach_to_packs(str(tmp_path), str(scan_dir))
    assert len(written) == 1
    pack = pack_mod.load_pack(written[0])
    assert pack['items'][0]['kind'] == 'translation'
    assert pack['srt'].endswith('EP001.srt')

    # a filled decision survives a rerun of the detector
    pack['items'][0]['decision'] = 'replace_text'
    pack['items'][0]['replacement'] = '你这混蛋在干什么'
    pack_mod.save_pack(written[0], pack)
    again = pack_mod.load_pack(review_failures.attach_to_packs(
        str(tmp_path), str(scan_dir))[0])
    assert again['items'][0]['decision'] == 'replace_text'
    assert again['items'][0]['replacement'] == '你这混蛋在干什么'


def _item(replacement_text='', **kwargs):
    base = {
        'id': 0, 'cue_index': 0, 'kind': 'translation', 'route': 'text',
        'failure': ['japanese_residual'], 'start': '00:00:05,000',
        'end': '00:00:06,500', 'seconds': {'start': 5.0, 'end': 6.5},
        'text': 'おまえなにしてんだ', 'source_text': 'おまえなにしてんだ',
        'asr_text': '', 'similarity': 0.0, 'length_ratio': 0.0,
        'context': {'prev': [], 'next': []},
        'suggested': {'new_start': None, 'new_end': None, 'new_text': ''},
        'decision': '', 'new_start': None, 'new_end': None,
        'replacement': '', 'decided_by': '', 'why': '', 'rejected': '',
    }
    base.update(kwargs)
    return base


def _pack(*items):
    return {'schema_version': 2, 'kind': 'review_pack', 'episode': 'EP001',
            'srt': 'EP001.srt', 'items': list(items)}


def _chat(payload):
    def chat(messages):
        return json.dumps(payload, ensure_ascii=False), ''
    return chat


def test_translation_rewrite_allowed_by_default_policy():
    pack = _pack(_item())
    chat = _chat({'decisions': [{'id': 0, 'decision': 'replace_text',
                                 'replacement': '你这混蛋在干什么'}]})

    stats = review_fill.fill_pack(
        pack, chat=chat,
        allowed_actions={'translation': {'keep', 'rewrite'}})

    assert stats['filled'] == 1
    assert pack['items'][0]['decision'] == 'replace_text'


def test_translation_rewrite_with_kana_is_rejected():
    pack = _pack(_item())
    chat = _chat({'decisions': [{'id': 0, 'decision': 'replace_text',
                                 'replacement': 'おまえ何してる'}]})

    stats = review_fill.fill_pack(
        pack, chat=chat,
        allowed_actions={'translation': {'keep', 'rewrite'}})

    assert stats['rejected'] == 1
    assert 'kana' in pack['items'][0]['rejected']


def test_short_translation_may_be_one_character():
    pack = _pack(_item(text='ウン', source_text='ウン'))
    chat = _chat({'decisions': [{'id': 0, 'decision': 'replace_text',
                                 'replacement': '嗯'}]})

    stats = review_fill.fill_pack(
        pack, chat=chat,
        allowed_actions={'translation': {'keep', 'rewrite'}})

    assert stats['filled'] == 1
    assert pack['items'][0]['replacement'] == '嗯'


def test_translation_stage_keeps_its_own_policy():
    """ASR policy (keep only) must not disable translation rewrites."""
    pack = _pack(_item())
    chat = _chat({'decisions': [{'id': 0, 'decision': 'replace_text',
                                 'replacement': '你这混蛋在干什么'}]})

    stats = review_fill.fill_pack(
        pack, chat=chat,
        allowed_actions={'asr_repair': {'keep'},
                         'translation': {'keep', 'rewrite'}})

    assert stats['filled'] == 1


def test_detector_to_qa_loop_clears_residue_without_collateral(tmp_path):
    """Offline end-to-end: QA flags residue → fill → apply → QA is clean."""
    import shutil, subprocess, sys

    from lib.review import apply as review_apply
    from lib.subtitle_io import read_subtitles, write_subtitles
    from lib.translation.qa import count_kana, qa_report

    zh_dir = tmp_path / 'translated' / 'zh'
    zh_dir.mkdir(parents=True)
    zh_cues = [
        {'start': '00:00:05,000', 'end': '00:00:06,000',
         'start_s': 5.0, 'end_s': 6.0, 'text': '快跑！'},
        {'start': '00:00:07,000', 'end': '00:00:08,000',
         'start_s': 7.0, 'end_s': 8.0, 'text': 'おまえなにしてんだ'},
        {'start': '00:00:09,000', 'end': '00:00:10,000',
         'start_s': 9.0, 'end_s': 10.0, 'text': '好'},
    ]
    zh_path = zh_dir / 'EP001.srt'
    write_subtitles(str(zh_path), zh_cues)
    ja_cues = [dict(c) for c in zh_cues]
    ja_cues[1] = dict(ja_cues[1], text='おまえなにしてんだ')

    qa = qa_report(ja_cues, zh_cues, glossary={})
    assert qa['summary']['japanese_residual'] == 1
    (zh_dir / 'EP001.qa.json').write_text(
        json.dumps(qa, ensure_ascii=False), encoding='utf-8')

    scan_dir = tmp_path / 'temp' / 'scans'
    packs = review_failures.attach_to_packs(str(tmp_path), str(scan_dir))
    pack = pack_mod.load_pack(packs[0])
    assert len(pack['items']) == 1

    def chat(messages):
        payload = json.loads(messages[1]['content'])
        decisions = [{'id': row['id'], 'decision': 'replace_text',
                      'replacement': '你这混蛋在干什么'} for row in payload]
        return json.dumps({'decisions': decisions}, ensure_ascii=False), ''

    fill_mod = __import__('lib.review.fill', fromlist=['fill_pack'])
    fill_mod.fill_pack(pack, chat=chat,
                       allowed_actions={'translation': {'keep', 'rewrite'}})
    result = review_apply.apply_pack(pack)
    assert result['applied'] == 1

    fixed = read_subtitles(str(zh_path), mark_garbled=False)
    after = qa_report(ja_cues, fixed, glossary={})
    assert after['summary']['japanese_residual'] == 0
    assert count_kana(fixed[1]['text']) == 0
    # the two untouched cues are byte-identical
    assert fixed[0]['text'] == '快跑！' and fixed[2]['text'] == '好'
