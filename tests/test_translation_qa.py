"""Tests for lib/translation/qa.py — objective translation checks."""

import pytest

from lib.translation.qa import (
    count_kana, cue_violations, glossary_misses, is_untranslated, qa_report,
)


def test_count_kana_and_echo():
    assert count_kana('おはよう') == 4
    assert count_kana('早上好') == 0
    assert is_untranslated('おはよう', 'おはよう')
    assert is_untranslated('おはよう', '1. おはよう')
    assert not is_untranslated('おはよう', '早上好')
    assert is_untranslated('おはよう', '')


def test_cue_violations_length_and_cps():
    cue = {'text': 'あ' * 25, 'start_s': 0.0, 'end_s': 10.0}
    rules = [v['rule'] for v in cue_violations(cue, max_chars=20,
                                               max_cps=10)]
    assert rules == ['max_chars']

    cue = {'text': '早' * 20, 'start_s': 0.0, 'end_s': 1.0}
    rules = [v['rule'] for v in cue_violations(cue, max_chars=20,
                                               max_cps=10)]
    assert rules == ['max_cps']

    # Zero/unknown duration: no CPS check, no crash.
    assert cue_violations({'text': '早', 'start_s': 1.0, 'end_s': 1.0}) == []


def test_glossary_misses():
    glossary = {'太一': '太一', 'アグモン': '亚古兽', 'デジモン': '数码兽'}
    assert glossary_misses('太一とアグモン', '太一和亚古兽', glossary) == []
    assert glossary_misses('太一とアグモン', '太一和亚古兽', None) == []
    assert glossary_misses('アグモン', '太一和数码兽', glossary) == ['アグモン']
    # Term absent from source is not a miss.
    assert glossary_misses('おはよう', '早上好', glossary) == []


def test_qa_report_aggregates_and_aligns():
    src = [
        {'text': 'おはよう', 'start_s': 1.0, 'end_s': 2.0},
        {'text': 'アグモン', 'start_s': 2.0, 'end_s': 4.0},
        {'text': '行くぞ', 'start_s': 4.0, 'end_s': 5.0},
    ]
    out = [
        {'text': '早上好', 'start_s': 1.0, 'end_s': 2.0},
        {'text': 'アグモン', 'start_s': 2.0, 'end_s': 4.0},   # echo + kana
        # third missing
    ]
    report = qa_report(src, out, glossary={'アグモン': '亚古兽'},
                       max_chars=20, max_cps=12)
    s = report['summary']
    assert s['total'] == 3
    assert s['output'] == 2
    assert s['missing'] == 1
    assert s['japanese_residual'] == 1
    assert s['echo'] == 1
    assert s['glossary_miss'] == 1
    assert s['violations'] == 2
    assert 'missing' in report['items'][1]['reasons']


def test_qa_report_clean_sample_passes():
    src = [{'text': 'おはよう', 'start_s': 0, 'end_s': 2}]
    out = [{'text': '早上好', 'start_s': 0, 'end_s': 2}]
    report = qa_report(src, out)
    assert report['summary']['violations'] == 0
    assert report['summary']['coverage'] == 1.0
