"""Tests for fix/oped_fill.py — blank-only fill, lyric templates, LLM IO."""

import json

import pytest

from fix import oped_fill as ofill
from fix.oped_fill import OpedFill


def _write_srt(path, cues):
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f'{i}\n{start} --> {end}\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload, ensure_ascii=False).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fill(tmp_path, **kwargs):
    return OpedFill(str(tmp_path), op_boundary=10, ed_boundary=10, **kwargs)


# ═══════════════════════════════════════════════════════════════
# LLM helpers
# ═══════════════════════════════════════════════════════════════

def test_call_llm_returns_content(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['body'] = json.loads(req.data.decode('utf-8'))
        captured['auth'] = dict(req.header_items())['Authorization']
        return _FakeResponse({'choices': [{'message': {'content': 'A'}}]})

    monkeypatch.setattr(ofill.urllib.request, 'urlopen', fake_urlopen)

    out = ofill._call_llm([{'role': 'user', 'content': 'x'}], 'key', 'm',
                          'http://x', max_tokens=123)

    assert out == 'A'
    assert captured['auth'] == 'Bearer key'
    assert captured['body']['model'] == 'm'
    assert captured['body']['max_tokens'] == 123


def test_call_llm_returns_none_on_error(monkeypatch, capsys):
    def boom(req, timeout=None):
        raise RuntimeError('offline')

    monkeypatch.setattr(ofill.urllib.request, 'urlopen', boom)

    assert ofill._call_llm([], 'k', 'm', 'http://x') is None
    assert 'API error' in capsys.readouterr().err


@pytest.mark.parametrize('response, expected', [
    ('{"a": 1}', {'a': 1}),
    ('```json\n[1, 2]\n```', [1, 2]),
    ('说明文字 [{"song_id": "OP1"}] 结束', [{'song_id': 'OP1'}]),
])
def test_parse_json_response_variants(response, expected):
    assert ofill._parse_json_response(response) == expected


def test_parse_json_response_garbage(capsys):
    assert ofill._parse_json_response('完全不是 JSON') is None
    assert ofill._parse_json_response('') is None
    assert 'Could not parse JSON' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Step 1 — boundaries
# ═══════════════════════════════════════════════════════════════

def test_step1_skip_uses_defaults(tmp_path):
    filler = OpedFill(str(tmp_path), op_boundary=95, ed_boundary=120,
                      skip_step1=True)

    bounds = filler.step1_detect_boundaries()

    assert bounds['confidence'] == 'fallback'
    assert bounds['op_end_s'] == 95.0
    assert bounds['ed_end_s'] == 120.0
    assert filler.boundaries == bounds


def test_step1_uses_api_detection(tmp_path, monkeypatch):
    sentinel = {'op_end_s': 45.0, 'ed_end_s': 60.0, 'confidence': 'api',
                'warnings': ['note']}
    monkeypatch.setattr(ofill, 'api_detect_boundaries',
                        lambda *a, **k: sentinel)
    filler = OpedFill(str(tmp_path))

    assert filler.step1_detect_boundaries() == sentinel
    assert filler.boundaries == sentinel


# ═══════════════════════════════════════════════════════════════
# Step 2 — instrumental vs vocal
# ═══════════════════════════════════════════════════════════════

def test_step2_dry_run_is_uncertain(tmp_path):
    filler = _fill(tmp_path, dry_run=True)
    assert filler.step2_classify() == {'OP': 'uncertain', 'ED': 'uncertain'}


def test_step2_without_video_assumes_vocal(tmp_path):
    filler = _fill(tmp_path)
    assert filler.step2_classify() == {'OP': 'vocal', 'ED': 'vocal'}


def _patch_media(monkeypatch, tmp_path, whisper_segs):
    video = tmp_path / 'ep.mkv'
    video.write_bytes(b'')
    monkeypatch.setattr(ofill, 'find_video', lambda *a, **k: str(video))

    def fake_extract(video_path, out_path, ss=None, duration=None, **kwargs):
        with open(out_path, 'wb'):
            pass

    monkeypatch.setattr(ofill, 'extract_audio_wav', fake_extract)
    monkeypatch.setattr(ofill, 'run_whisper',
                        lambda *a, **k: list(whisper_segs))


def test_step2_classifies_from_whisper_output(tmp_path, monkeypatch):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:01,000', '00:00:02,000', 'x'),
        ('00:03:50,000', '00:03:51,000', 'y'),
    ])
    _patch_media(monkeypatch, tmp_path, [{'text': 'ラララ', 'start_s': 0.0}])
    filler = _fill(tmp_path, video_dir=str(tmp_path))
    filler.boundaries = {'op_end_s': 10, 'ed_end_s': 10}
    monkeypatch.setattr(filler, '_api_classify',
                        lambda region, outputs: 'vocal')

    assert filler.step2_classify() == {'OP': 'vocal', 'ED': 'vocal'}


def test_step2_without_whisper_output_is_uncertain(tmp_path, monkeypatch):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:01,000', '00:00:02,000', 'x'),
        ('00:03:50,000', '00:03:51,000', 'y'),
    ])
    _patch_media(monkeypatch, tmp_path, [])
    filler = _fill(tmp_path, video_dir=str(tmp_path))
    filler.boundaries = {'op_end_s': 10, 'ed_end_s': 10}

    assert filler.step2_classify() == {'OP': 'uncertain', 'ED': 'uncertain'}


@pytest.mark.parametrize('response, expected', [
    ('A：随机假名', 'instrumental'),
    ('B 有语义相似', 'vocal'),
    ('C', 'uncertain'),
    ('', 'uncertain'),
])
def test_api_classify_maps_letters(monkeypatch, tmp_path, response, expected):
    filler = _fill(tmp_path)
    monkeypatch.setattr(ofill, '_call_llm', lambda *a, **k: response)

    out = filler._api_classify('OP', [
        {'ep': 'EP001.srt', 'region': 'OP', 'text': 'ララ', 'start_s': 0.0},
        {'ep': 'EP002.srt', 'region': 'OP', 'text': 'ララ', 'start_s': 0.0},
    ])

    assert out == expected


def test_api_classify_prompt_lists_episodes(monkeypatch, tmp_path):
    filler = _fill(tmp_path)
    seen = {}

    def fake_llm(messages, *args, **kwargs):
        seen['prompt'] = messages[0]['content']
        return 'B'

    monkeypatch.setattr(ofill, '_call_llm', fake_llm)
    filler._api_classify('OP', [
        {'ep': 'EP001.srt', 'region': 'OP', 'text': 'ララ', 'start_s': 0.0},
        {'ep': 'EP002.srt', 'region': 'OP', 'text': 'ルル', 'start_s': 0.0},
    ])

    assert 'OP（片头/片尾曲）' in seen['prompt']
    assert '=== EP001.srt ===' in seen['prompt']
    assert '[0.0s] ララ' in seen['prompt']


# ═══════════════════════════════════════════════════════════════
# Step 3 — templates
# ═══════════════════════════════════════════════════════════════

def test_api_translate_returns_templates(monkeypatch, tmp_path):
    filler = _fill(tmp_path)
    payload = [{'song_id': 'OP1', 'episodes': 'all',
                'lyrics': [{'ja': '歌', 'zh': '歌', 'time_s': 1.0}]}]
    monkeypatch.setattr(ofill, '_call_llm',
                        lambda *a, **k: json.dumps(payload, ensure_ascii=False))

    assert filler._api_translate('OP', [
        {'ep': 'EP001.srt', 'text': '歌', 'start_s': 1.0, 'end_s': 2.0},
    ]) == payload


def test_api_translate_rejects_garbage(monkeypatch, tmp_path):
    filler = _fill(tmp_path)
    monkeypatch.setattr(ofill, '_call_llm', lambda *a, **k: '不是 JSON')
    assert filler._api_translate('OP', [{'ep': 'x', 'text': 'y',
                                         'start_s': 0, 'end_s': 1}]) == []


def test_match_lyric_region_time_and_episode_range(tmp_path):
    filler = _fill(tmp_path)
    filler.lyric_templates = [
        {'song_id': 'OP1', 'episodes': '010-052',
         'lyrics': [{'ja': 'a', 'zh': '甲', 'time_s': 5.0}]},
        {'song_id': 'ED1', 'episodes': 'all',
         'lyrics': [{'ja': 'b', 'zh': '乙', 'time_s': 5.0}]},
    ]

    # wrong episode range for OP1
    assert filler._match_lyric('OP', 5.0, 1) is None
    assert filler._match_lyric('OP', 5.0, 20)['zh'] == '甲'
    assert filler._match_lyric('OP', 9.0, 20) is None       # > 3s
    assert filler._match_lyric('ED', 6.0, 1)['zh'] == '乙'


def test_apply_templates_fills_only_blank_op_ed_cues(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:02,000', '00:00:03,000', ''),           # blank OP → fill
        ('00:00:04,000', '00:00:05,000', '本編セリフ'),  # has text → keep
        ('00:03:00,000', '00:03:01,000', 'これは本編'),  # outside → keep
        ('00:03:50,000', '00:03:51,000', ''),           # blank ED → fill
    ])
    filler = _fill(tmp_path)
    filler.boundaries = {'op_end_s': 10, 'ed_end_s': 10}
    filler.lyric_templates = [
        {'song_id': 'OP1', 'episodes': 'all',
         'lyrics': [{'ja': '歌', 'zh': '第一句', 'time_s': 2.0}]},
        {'song_id': 'ED1', 'episodes': 'all',
         'lyrics': [{'ja': '終', 'zh': '片尾句', 'time_s': 230.0}]},
    ]

    filler._apply_templates(['EP001.srt'])
    fixes = filler.fixes

    assert len(fixes) == 2
    assert {f['replacement'] for f in fixes} == {'第一句', '片尾句'}
    assert {f['action'] for f in fixes} == {'fill_blank'}
    assert all(f['original'] == '' for f in fixes)

    from lib.subtitle_io import read_subtitles
    cues = read_subtitles(str(tmp_path / 'EP001.srt'), mark_garbled=False)
    assert cues[0]['text'] == '第一句'
    assert cues[1]['text'] == '本編セリフ'
    assert cues[2]['text'] == 'これは本編'
    assert cues[3]['text'] == '片尾句'


def test_apply_templates_dry_run_does_not_write(tmp_path):
    _write_srt(tmp_path / 'EP001.srt', [
        ('00:00:02,000', '00:00:03,000', ''),
        ('00:03:50,000', '00:03:51,000', '終'),
    ])
    filler = _fill(tmp_path, dry_run=True)
    filler.boundaries = {'op_end_s': 10, 'ed_end_s': 10}
    filler.lyric_templates = [
        {'song_id': 'OP1', 'episodes': 'all',
         'lyrics': [{'ja': '歌', 'zh': '第一句', 'time_s': 2.0}]},
    ]

    filler._apply_templates(['EP001.srt'])

    assert len(filler.fixes) == 1
    from lib.subtitle_io import read_subtitles
    cues = read_subtitles(str(tmp_path / 'EP001.srt'), mark_garbled=False)
    assert cues[0]['text'] == ''


# ═══════════════════════════════════════════════════════════════
# run
# ═══════════════════════════════════════════════════════════════

def test_run_calls_steps_in_order(tmp_path, monkeypatch):
    calls = []
    filler = _fill(tmp_path)

    def step1(self):
        calls.append('step1')
        self.boundaries = {'op_end_s': 10, 'ed_end_s': 5,
                           'confidence': 'x'}
        return self.boundaries

    def step2(self):
        calls.append('step2')
        self.classification = {'OP': 'vocal', 'ED': 'instrumental'}
        return self.classification

    def step3(self):
        calls.append('step3')
        self.fixes = [{'action': 'fill_blank', 'file': 'EP001.srt'}]
        return self.fixes

    monkeypatch.setattr(OpedFill, 'step1_detect_boundaries', step1)
    monkeypatch.setattr(OpedFill, 'step2_classify', step2)
    monkeypatch.setattr(OpedFill, 'step3_fill', step3)

    summary = filler.run()

    assert calls == ['step1', 'step2', 'step3']
    assert summary['classification'] == {'OP': 'vocal', 'ED': 'instrumental'}
    assert summary['total_fixes'] == 1
    assert summary['templates'] == 0
