"""Tests for lib/llm.py — JSON extraction and chat client (network mocked)."""

import json
import urllib.error

import pytest

from lib import llm


# ═══════════════════════════════════════════════════════════════
# JSON extraction
# ═══════════════════════════════════════════════════════════════

def test_extract_plain_array():
    assert llm.extract_json_array('[{"a": 1}, {"b": 2}]') == [{'a': 1},
                                                               {'b': 2}]


def test_extract_from_markdown_fence():
    text = '说明\n```json\n[{"a": 1}]\n```\n结束'
    assert llm.extract_json_array(text) == [{'a': 1}]


def test_extract_finds_array_inside_prose():
    text = 'Here you go: [1, 2, 3] and some trailing words.'
    assert llm.extract_json_array(text) == [1, 2, 3]


def test_extract_tolerates_trailing_comma():
    assert llm.extract_json_array('[{"a": 1},]') == [{'a': 1}]


def test_extract_handles_brackets_inside_strings():
    text = '[{"note": "array [not nested]"}]'
    assert llm.extract_json_array(text) == [{'note': 'array [not nested]'}]


def test_extract_returns_none_without_array():
    assert llm.extract_json_array('no json here') is None
    assert llm.extract_json_array('[unclosed') is None
    assert llm.extract_json_array('') is None


# ═══════════════════════════════════════════════════════════════
# call_chat (network mocked)
# ═══════════════════════════════════════════════════════════════

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


def test_call_chat_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(llm, 'LLM_API_KEY', '')
    assert llm.call_chat([{'role': 'user', 'content': 'hi'}]) is None


def test_call_chat_returns_content(monkeypatch):
    monkeypatch.setattr(llm, 'LLM_API_KEY', 'key')
    monkeypatch.setattr(llm, 'LLM_MODEL', 'model')
    monkeypatch.setattr(llm, 'LLM_BASE_URL', 'http://example.test/v1')
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured['url'] = req.full_url
        captured['body'] = json.loads(req.data.decode('utf-8'))
        captured['auth'] = req.get_header('Authorization')
        return FakeResponse({'choices': [{'message': {'content': 'hello'}}]})

    monkeypatch.setattr(llm.urllib.request, 'urlopen', fake_urlopen)
    out = llm.call_chat([{'role': 'user', 'content': 'hi'}],
                        temperature=0.0, max_tokens=32)

    assert out == 'hello'
    assert captured['url'] == 'http://example.test/v1/chat/completions'
    assert captured['body']['model'] == 'model'
    assert captured['body']['max_tokens'] == 32
    assert captured['auth'] == 'Bearer key'


def test_call_chat_returns_empty_string_for_empty_content(monkeypatch,
                                                          capsys):
    monkeypatch.setattr(llm, 'LLM_API_KEY', 'key')
    monkeypatch.setattr(llm.urllib.request, 'urlopen',
                        lambda req, timeout=0: FakeResponse({'choices': [
                            {'message': {'content': '',
                                         'reasoning_content': 'thinking'},
                             'finish_reason': 'length'}]}))
    out = llm.call_chat([{'role': 'user', 'content': 'hi'}])
    assert out == ''
    assert '空 content' in capsys.readouterr().err


def test_call_chat_retries_then_returns_none(monkeypatch):
    monkeypatch.setattr(llm, 'LLM_API_KEY', 'key')
    calls = []

    def boom(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 500, 'boom', {}, None)

    monkeypatch.setattr(llm.urllib.request, 'urlopen', boom)
    assert llm.call_chat([{'role': 'user', 'content': 'hi'}],
                         retries=1, delay=0) is None
    assert len(calls) == 2


def test_call_chat_disables_reasoning_by_default(monkeypatch):
    monkeypatch.setattr(llm, 'LLM_API_KEY', 'key')
    monkeypatch.setattr(llm, 'LLM_REASONING_EFFORT', 'none')
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured['body'] = json.loads(req.data.decode('utf-8'))
        return FakeResponse({'choices': [{'message': {'content': 'ok'}}]})

    monkeypatch.setattr(llm.urllib.request, 'urlopen', fake_urlopen)
    llm.call_chat([{'role': 'user', 'content': 'hi'}])
    assert captured['body']['reasoning_effort'] == 'none'
