"""Tests for translation repair (only QA-flagged cues)."""

import json

import translate_srt as tr
from lib.translation.qa import qa_report


def _srt(*texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n'
                      f'{text}\n')
    return '\n'.join(blocks)


def _write_qa(src_path, out_path, qa_path):
    src = tr.parse_subtitles(str(src_path), mark_garbled=False)
    out = tr.parse_subtitles(str(out_path), mark_garbled=False)
    report = qa_report(src, out, max_chars=20, max_cps=12)
    qa_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    return report


def test_repair_only_flagged_cues(tmp_path, monkeypatch):
    src = tmp_path / 'src.srt'
    out = tmp_path / 'out.srt'
    qa = tmp_path / 'qa.json'
    src.write_text(_srt('おはよう', 'こんにちは'), encoding='utf-8')
    out.write_text(_srt('おはよう', '你好'), encoding='utf-8')
    report = _write_qa(src, out, qa)
    assert report['summary']['echo'] == 1

    calls = []

    def fake_llm(messages, *args, **kwargs):
        calls.append(messages)
        return '["早上好"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    fixed, new_report = tr.repair_file(
        str(src), str(out), str(qa), {}, 'key', 'model', 'url')

    assert fixed == 1
    assert len(calls) == 1
    text = out.read_text(encoding='utf-8')
    assert '早上好' in text
    assert '你好' in text
    assert new_report['summary']['echo'] == 0


def test_repair_prompt_contains_ja_to_zh_mapping(tmp_path, monkeypatch):
    src = tmp_path / 'src.srt'
    out = tmp_path / 'out.srt'
    qa = tmp_path / 'qa.json'
    src.write_text(_srt('タケル'), encoding='utf-8')
    out.write_text(_srt('タケル'), encoding='utf-8')
    _write_qa(src, out, qa)
    seen = {}

    def fake_llm(messages, *args, **kwargs):
        seen['text'] = messages[0]['content'] + messages[1]['content']
        return '["阿武"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    tr.repair_file(str(src), str(out), str(qa), {'タケル': '阿武'},
                   'key', 'model', 'url')
    assert 'タケル→阿武' in seen['text']


def test_repair_noop_when_clean(tmp_path, monkeypatch):
    src = tmp_path / 'src.srt'
    out = tmp_path / 'out.srt'
    qa = tmp_path / 'qa.json'
    src.write_text(_srt('おはよう'), encoding='utf-8')
    out.write_text(_srt('早上好'), encoding='utf-8')
    _write_qa(src, out, qa)

    monkeypatch.setattr(tr, '_call_llm',
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError('must not call LLM')))
    fixed, report = tr.repair_file(
        str(src), str(out), str(qa), {}, 'key', 'model', 'url')
    assert fixed == 0
    assert report['summary']['violations'] == 0
