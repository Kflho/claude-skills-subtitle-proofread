"""Bili23 MCP client: config handling, JSON-RPC shape, episode selection."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from acquire import bili23  # noqa: E402


def _config(tmp_path, **mcp):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({
        'MCP': {'mcp_enabled': False, 'mcp_port': 23330, 'mcp_token': '',
                **mcp},
        'Download': {'download_path': 'D:/Video/Test'},
        'Cookie': {'is_login': True},
    }, ensure_ascii=False), encoding='utf-8')
    return path


def test_resolve_config_prefers_explicit_path(tmp_path):
    path = _config(tmp_path)
    assert bili23.resolve_config_path(str(path)) == path


def test_enable_mcp_sets_flag_and_token_with_backup(tmp_path):
    path = _config(tmp_path)
    result = bili23.enable_mcp(path)

    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['MCP']['mcp_enabled'] is True
    assert saved['MCP']['mcp_token']
    assert path.with_suffix('.json.bak').exists()
    assert result['token_set'] is True
    # the token value must never be part of the returned summary
    assert saved['MCP']['mcp_token'] not in json.dumps(result)


def test_enable_mcp_keeps_existing_token(tmp_path):
    path = _config(tmp_path, mcp_token='already-there')
    bili23.enable_mcp(path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['MCP']['mcp_token'] == 'already-there'


def test_select_ids_all_and_specific():
    parsed = {'episodes': [{'episode_id': '1'}, {'episode_id': '2'}]}
    assert bili23._select_ids(parsed, 'all') == ['1', '2']
    assert bili23._select_ids(parsed, '2') == ['2']
    with pytest.raises(bili23.MCPError):
        bili23._select_ids(parsed, '9')


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_mcp_call_posts_bearer_and_returns_structured(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen['url'] = request.full_url
        seen['auth'] = request.headers.get('Authorization')
        seen['body'] = json.loads(request.data)
        return _FakeResponse({'jsonrpc': '2.0', 'id': 2, 'result': {
            'structuredContent': {'ok': True}}})

    monkeypatch.setattr(bili23.urllib.request, 'urlopen', fake_urlopen)
    client = bili23.Bili23MCP(23330, 'secret-token')
    data = client.call('get_login_status')

    assert data == {'ok': True}
    assert seen['url'] == 'http://127.0.0.1:23330/mcp'
    assert seen['auth'] == 'Bearer secret-token'
    assert seen['body']['method'] == 'tools/call'
    assert seen['body']['params']['name'] == 'get_login_status'


def test_mcp_iserror_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return _FakeResponse({'jsonrpc': '2.0', 'id': 2, 'result': {
            'isError': True,
            'content': [{'type': 'text', 'text': 'nope'}]}})

    monkeypatch.setattr(bili23.urllib.request, 'urlopen', fake_urlopen)
    with pytest.raises(bili23.MCPError, match='nope'):
        bili23.Bili23MCP(23330, 't').call('parse_url', {'url': 'x'})
