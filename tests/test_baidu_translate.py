"""Tests for fix/translate_srt.py — Baidu translate layer (mocked HTTP)."""

import hashlib
import json
import sys
import urllib.parse
import urllib.error

import pytest

from fix import translate_srt as baidu


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
# Credentials
# ═══════════════════════════════════════════════════════════════

def test_load_credentials_reads_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(baidu.os.path, 'expanduser',
                        lambda path: str(tmp_path / 'home' / '.baidu_translate'))
    monkeypatch.setattr(baidu, 'BAIDU_APPID', '')
    monkeypatch.setattr(baidu, 'BAIDU_SECRET', '')
    monkeypatch.setattr(baidu, 'BAIDU_API_ENDPOINT', '')
    config = tmp_path / 'home' / '.baidu_translate'
    config.parent.mkdir()
    config.write_text('BAIDU_APPID=file-id\nBAIDU_SECRET=file-secret\n'
                      'BAIDU_ENDPOINT=https://proxy.example/api\n',
                      encoding='utf-8')

    assert baidu.load_credentials() == ('file-id', 'file-secret',
                                        'https://proxy.example/api')


def test_load_credentials_env_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setattr(baidu.os.path, 'expanduser',
                        lambda path: str(tmp_path / 'home' / '.baidu_translate'))
    monkeypatch.setattr(baidu, 'BAIDU_APPID', 'env-id')
    monkeypatch.setattr(baidu, 'BAIDU_SECRET', 'env-secret')
    monkeypatch.setattr(baidu, 'BAIDU_API_ENDPOINT', baidu.BAIDU_API)
    config = tmp_path / 'home' / '.baidu_translate'
    config.parent.mkdir()
    config.write_text('BAIDU_APPID=file-id\nBAIDU_SECRET=file-secret\n',
                      encoding='utf-8')

    appid, secret, endpoint = baidu.load_credentials()
    assert (appid, secret) == ('env-id', 'env-secret')
    assert endpoint == baidu.BAIDU_API


def test_load_credentials_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(baidu.os.path, 'expanduser',
                        lambda path: str(tmp_path / 'nohome'))
    monkeypatch.setattr(baidu, 'BAIDU_APPID', '')
    monkeypatch.setattr(baidu, 'BAIDU_SECRET', '')
    monkeypatch.setattr(baidu, 'BAIDU_API_ENDPOINT', baidu.BAIDU_API)

    assert baidu.load_credentials() == ('', '', baidu.BAIDU_API)


# ═══════════════════════════════════════════════════════════════
# API call
# ═══════════════════════════════════════════════════════════════

def test_baidu_translate_signs_request_and_maps_language(monkeypatch, capsys):
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured['url'] = req.get_full_url()
        captured['query'] = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.get_full_url()).query)
        return _FakeResponse({'trans_result': [{'dst': '你好'}]})

    monkeypatch.setattr(baidu.urllib.request, 'urlopen', fake_urlopen)

    out = baidu.baidu_translate('こんにちは', 'appid', 'secret',
                                source='ja', target='zh',
                                endpoint='https://api.example')

    assert out == '你好'
    query = captured['query']
    assert query['from'] == ['jp']          # ja → Baidu code jp
    assert query['to'] == ['zh']
    assert query['appid'] == ['appid']
    salt = query['salt'][0]
    expected = hashlib.md5(
        ('appid' + 'こんにちは' + salt + 'secret').encode('utf-8')).hexdigest()
    assert query['sign'] == [expected]


def test_baidu_translate_error_code_returns_none(monkeypatch, capsys):
    monkeypatch.setattr(
        baidu.urllib.request, 'urlopen',
        lambda req, timeout=None, context=None:
            _FakeResponse({'error_code': '54003',
                           'error_msg': 'Invalid Access Limit'}))

    assert baidu.baidu_translate('x', 'a', 's', endpoint='https://x') is None
    err = capsys.readouterr().err
    assert '54003' in err and 'Invalid Access Limit' in err


def test_baidu_translate_http_error_returns_none(monkeypatch, capsys):
    def raise_http(req, timeout=None, context=None):
        raise urllib.error.HTTPError('https://x', 502, 'Bad Gateway', {}, None)

    monkeypatch.setattr(baidu.urllib.request, 'urlopen', raise_http)

    assert baidu.baidu_translate('x', 'a', 's', endpoint='https://x') is None
    assert 'HTTP error 502' in capsys.readouterr().err


def test_baidu_translate_generic_error_returns_none(monkeypatch, capsys):
    def boom(req, timeout=None, context=None):
        raise RuntimeError('offline')

    monkeypatch.setattr(baidu.urllib.request, 'urlopen', boom)

    assert baidu.baidu_translate('x', 'a', 's', endpoint='https://x') is None
    assert 'Translation error' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Language detection
# ═══════════════════════════════════════════════════════════════

def test_detect_source_language():
    def cues(*texts):
        return [{'text': t} for t in texts]

    assert baidu.detect_source_language(cues('こんにちは')) == 'ja'
    assert baidu.detect_source_language(cues('你好世界')) == 'zh'
    assert baidu.detect_source_language(cues('hello world')) == 'en'
    assert baidu.detect_source_language([]) == 'auto'
    assert baidu.detect_source_language(cues('')) == 'auto'


# ═══════════════════════════════════════════════════════════════
# CLI pipeline
# ═══════════════════════════════════════════════════════════════

def test_main_requires_credentials(monkeypatch):
    monkeypatch.setattr(baidu, 'load_credentials', lambda: ('', '', None))
    monkeypatch.setattr(sys, 'argv', ['translate_srt.py', 'in.srt',
                                      '--output', 'out.srt'])
    with pytest.raises(SystemExit) as exc:
        baidu.main()
    assert exc.value.code == 1


def test_main_dry_run_writes_nothing(monkeypatch, tmp_path, capsys):
    src = tmp_path / 'in.srt'
    src.write_text(_srt('こんにちは', 'さようなら'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    monkeypatch.setattr(baidu, 'load_credentials',
                        lambda: ('id', 'secret', None))
    monkeypatch.setattr(sys, 'argv', ['translate_srt.py', str(src),
                                      '--output', str(out), '--dry-run'])

    baidu.main()

    assert not out.exists()
    err = capsys.readouterr().err
    assert 'DRY RUN' in err
    assert 'Cues to translate: 2/2' in err


def test_main_translates_and_writes(monkeypatch, tmp_path):
    src = tmp_path / 'in.srt'
    src.write_text(_srt('こんにちは', 'さようなら'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    monkeypatch.setattr(baidu, 'load_credentials',
                        lambda: ('id', 'secret', None))
    monkeypatch.setattr(baidu, 'baidu_translate',
                        lambda text, *a, **k: f'译:{text}')
    monkeypatch.setattr(sys, 'argv', [
        'translate_srt.py', str(src), '--output', str(out), '--source', 'ja',
        '--batch', '1', '--delay', '0'])

    baidu.main()

    text = out.read_text(encoding='utf-8')
    assert '译:こんにちは' in text
    assert '译:さようなら' in text
    assert '00:00:02' in text and '00:00:03' in text


def test_main_keeps_original_on_failure(monkeypatch, tmp_path, capsys):
    src = tmp_path / 'in.srt'
    src.write_text(_srt('こんにちは'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    monkeypatch.setattr(baidu, 'load_credentials',
                        lambda: ('id', 'secret', None))
    monkeypatch.setattr(baidu, 'baidu_translate', lambda *a, **k: None)
    monkeypatch.setattr(sys, 'argv', [
        'translate_srt.py', str(src), '--output', str(out), '--source', 'ja',
        '--batch', '1', '--delay', '0'])

    baidu.main()

    assert 'こんにちは' in out.read_text(encoding='utf-8')
    assert 'WARNING: 1 cues failed' in capsys.readouterr().err


def test_main_resume_skips_existing_cues(monkeypatch, tmp_path, capsys):
    src = tmp_path / 'in.srt'
    src.write_text(_srt('こんにちは', 'さようなら'), encoding='utf-8')
    out = tmp_path / 'out.srt'
    out.write_text(_srt('你好'), encoding='utf-8')   # first cue already done
    calls = []
    monkeypatch.setattr(baidu, 'load_credentials',
                        lambda: ('id', 'secret', None))
    monkeypatch.setattr(baidu, 'baidu_translate',
                        lambda text, *a, **k: calls.append(text) or f'译:{text}')
    monkeypatch.setattr(sys, 'argv', [
        'translate_srt.py', str(src), '--output', str(out), '--source', 'ja',
        '--resume', '--batch', '2', '--delay', '0'])

    baidu.main()

    # read_subtitles normalizes timestamps, so the start key matches cue 1
    assert calls == ['さようなら']
    assert 'Resume: 1 already translated' in capsys.readouterr().err
