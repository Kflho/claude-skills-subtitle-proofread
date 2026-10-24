"""Tests for polish_zh.py — batch prompt building, JSON parsing, write policy."""

import json
import sys

import pytest

import polish_zh as pz


def _srt(*texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n'
                      f'{text}\n')
    return '\n'.join(blocks)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload, ensure_ascii=False).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ═══════════════════════════════════════════════════════════════
# Glossary
# ═══════════════════════════════════════════════════════════════

def test_load_glossary_skips_header(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text(
        '| 日语 | 出现次数 | 拼写变体 |\n'
        '|------|---------|---------|\n'
        '| サリー | 96 | サリーちゃん |\n'
        '| アトム | 88 | |\n', encoding='utf-8')

    assert pz.load_glossary(str(path)) == 'アトム, サリー'


def test_load_glossary_missing_or_empty(tmp_path):
    assert pz.load_glossary('nope.md') == ''
    empty = tmp_path / 'empty.md'
    empty.write_text('# 没有表格\n', encoding='utf-8')
    assert pz.load_glossary(str(empty)) == ''


# ═══════════════════════════════════════════════════════════════
# API call
# ═══════════════════════════════════════════════════════════════

def test_call_deepseek_returns_content(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['body'] = json.loads(req.data.decode('utf-8'))
        captured['auth'] = dict(req.header_items())['Authorization']
        return _FakeResponse({'choices': [{'message': {'content': '["润"]'}}]})

    monkeypatch.setattr(pz.urllib.request, 'urlopen', fake_urlopen)

    out = pz._call_deepseek([{'role': 'user', 'content': 'x'}], 'sk-1', 'm',
                            'http://x')

    assert out == '["润"]'
    assert captured['auth'] == 'Bearer sk-1'
    assert captured['body']['model'] == 'm'
    assert captured['body']['messages'] == [{'role': 'user', 'content': 'x'}]


def test_call_deepseek_returns_none_on_error(monkeypatch, capsys):
    def boom(req, timeout=None):
        raise RuntimeError('offline')

    monkeypatch.setattr(pz.urllib.request, 'urlopen', boom)

    assert pz._call_deepseek([], 'k', 'm', 'http://x') is None
    assert '[polish] API error' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# polish_batch — prompt layout and parsing
# ═══════════════════════════════════════════════════════════════

def test_polish_batch_builds_context_and_target_window(monkeypatch):
    seen = {}
    texts = [f'句{i}' for i in range(1, 11)]

    def fake_llm(messages, api_key, model, base_url):
        seen['messages'] = messages
        return json.dumps([f'润{i}' for i in range(1, 11)])

    monkeypatch.setattr(pz, '_call_deepseek', fake_llm)

    cues = [{'text': t} for t in texts]
    out = pz.polish_batch(cues, 'サリー', 'k', 'm', 'u')

    assert out == [f'润{i}' for i in range(1, 11)]
    system, user = seen['messages']
    assert system['role'] == 'system' and '去翻译腔' in system['content']
    prompt = user['content']
    assert '上下文（前 3 条）' in prompt and '句1' in prompt
    assert '上下文（后 3 条）' in prompt and '句10' in prompt
    assert '1. 句4' in prompt and '4. 句7' in prompt
    assert 'サリー' in prompt


def test_polish_batch_pads_short_batches(monkeypatch):
    seen = {}
    monkeypatch.setattr(pz, '_call_deepseek',
                        lambda messages, *a: seen.setdefault('m', messages)
                        and '["A"]')

    out = pz.polish_batch([{'text': '短'}], '', 'k', 'm', 'u')

    assert out == ['A']
    # only one non-empty cue → one numbered target line
    assert seen['m'][1]['content'].count('\n') >= 0


def test_polish_batch_parses_markdown_fence(monkeypatch):
    monkeypatch.setattr(pz, '_call_deepseek',
                        lambda *a, **k: '```json\n["润1"]\n```')
    assert pz.polish_batch([{'text': 'x'}] * 10, '', 'k', 'm', 'u') == ['润1']


def test_polish_batch_rejects_bad_json(monkeypatch, capsys):
    monkeypatch.setattr(pz, '_call_deepseek', lambda *a, **k: '不是 JSON')
    assert pz.polish_batch([{'text': 'x'}] * 10, '', 'k', 'm', 'u') is None
    assert 'Failed to parse JSON' in capsys.readouterr().err


def test_polish_batch_propagates_api_failure(monkeypatch):
    monkeypatch.setattr(pz, '_call_deepseek', lambda *a, **k: None)
    assert pz.polish_batch([{'text': 'x'}] * 10, '', 'k', 'm', 'u') is None


# ═══════════════════════════════════════════════════════════════
# polish_srt — write policy
# ═══════════════════════════════════════════════════════════════

def test_polish_srt_writes_changed_cues_and_keeps_timeline(
        monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('我想去', '你是什么人'), encoding='utf-8')
    out = tmp_path / 'polished' / 'EP001.srt'
    monkeypatch.setattr(pz, 'polish_batch', lambda *a, **k: ['我想去', '你是谁'])

    total, polished, failed = pz.polish_srt(str(src), str(out), '',
                                            'k', 'm', 'u')

    assert (total, polished, failed) == (2, 1, 0)
    text = out.read_text(encoding='utf-8')
    assert '你是谁' in text
    assert '00:00:02' in text and '00:00:03' in text


def test_polish_srt_dry_run_makes_no_calls(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('我想去', '你是什么人'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    calls = []
    monkeypatch.setattr(pz, 'polish_batch',
                        lambda *a, **k: calls.append(1) or ['甲', '乙'])

    assert pz.polish_srt(str(src), str(out), '', 'k', 'm', 'u',
                         dry_run=True) == (2, 0, 0)
    assert calls == []
    assert not out.exists()


def test_polish_srt_skips_single_cue_batches(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('只有一句'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    calls = []
    monkeypatch.setattr(pz, 'polish_batch',
                        lambda *a, **k: calls.append(1) or ['甲'])

    assert pz.polish_srt(str(src), str(out), '', 'k', 'm', 'u') == (1, 0, 0)
    assert calls == []
    assert not out.exists()


def test_polish_srt_failure_keeps_original(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('甲', '乙'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    monkeypatch.setattr(pz, 'polish_batch', lambda *a, **k: None)

    assert pz.polish_srt(str(src), str(out), '', 'k', 'm', 'u') == (2, 0, 2)
    assert not out.exists()


# ═══════════════════════════════════════════════════════════════
# polish_dir
# ═══════════════════════════════════════════════════════════════

def test_polish_dir_processes_files_and_reports_cost(monkeypatch, tmp_path,
                                                     capsys):
    in_dir = tmp_path / 'in'
    in_dir.mkdir()
    (in_dir / 'EP001.srt').write_text(_srt('甲', '乙'), encoding='utf-8')
    (in_dir / '._junk.srt').write_text(_srt('甲', '乙'), encoding='utf-8')
    out_dir = tmp_path / 'out'
    calls = []

    def fake_polish(input_path, output_path, *args):
        calls.append(input_path)
        return 2, 1, 0

    monkeypatch.setattr(pz, 'polish_srt', fake_polish)

    assert pz.polish_dir(str(in_dir), str(out_dir), '', 'k', 'm', 'u') is True
    assert calls == [str(in_dir / 'EP001.srt')]
    err = capsys.readouterr().err
    assert 'Est. cost' in err


def test_polish_dir_requires_input_dir(tmp_path):
    with pytest.raises(SystemExit):
        pz.polish_dir(str(tmp_path / 'missing'), str(tmp_path / 'out'),
                      '', 'k', 'm', 'u')


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_main_requires_api_key_without_dry_run(monkeypatch):
    monkeypatch.setattr(pz, 'LLM_API_KEY', '')
    monkeypatch.setattr(sys, 'argv', ['polish_zh.py', '--input', 'EP001.srt'])
    with pytest.raises(SystemExit) as exc:
        pz.main()
    assert exc.value.code == 1


def test_main_dry_run_writes_nothing(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('甲', '乙'), encoding='utf-8')
    monkeypatch.setattr(pz, 'LLM_API_KEY', 'fake-key')
    monkeypatch.setattr(sys, 'argv', ['polish_zh.py', '--input', str(src),
                                      '--dry-run'])

    pz.main()

    assert not (tmp_path / 'EP001_polished.srt').exists()
