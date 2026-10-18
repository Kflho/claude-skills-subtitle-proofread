"""Tests for lib/oped_detect.py — JSON parsing, normalization, fallback."""

import json

import pytest

from lib import oped_detect
from lib.oped_detect import (
    _build_boundary_prompt, _extract_cues_for_boundary, _fallback_boundaries,
    _format_cue_list, _normalize_result, _parse_json_response,
    detect_boundaries,
)


# ═══════════════════════════════════════════════════════════════
# JSON parsing / normalization
# ═══════════════════════════════════════════════════════════════

def test_parse_plain_json():
    assert _parse_json_response('{"op": {"start_s": 0}}') == {
        'op': {'start_s': 0}}


def test_parse_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert _parse_json_response(text) == {'a': 1}


def test_parse_json_inside_prose():
    text = '说明...\n{"op": {"end_s": 90}}\n后面还有'
    assert _parse_json_response(text) == {'op': {'end_s': 90}}


def test_parse_invalid_returns_none(capsys):
    assert _parse_json_response('not json') is None
    assert _parse_json_response('') is None


def test_normalize_fills_defaults():
    result = _normalize_result({
        'op': {'start_s': 5, 'end_s': 90},
        'ed': {'start_s': 1300, 'end_s': 1380},
        'preview': {'exists': True, 'start_s': 1380, 'end_s': 1440},
        'overall_confidence': 'high',
        'per_episode_notes': {'EP001.srt': '正常'},
        'warnings': ['w'],
    })
    assert result['op_start_s'] == 5.0
    assert result['preview_end_s'] == 1440.0
    assert result['confidence'] == 'high'

    defaults = _normalize_result({})
    assert defaults['op_end_s'] == float(oped_detect.FALLBACK_OP_SEC)
    assert defaults['ed_start_s'] is None
    assert defaults['preview_end_s'] is None
    assert defaults['confidence'] == 'medium'


def test_preview_without_exists_is_ignored():
    result = _normalize_result({'preview': {'end_s': 1400}})
    assert result['preview_end_s'] is None


def test_fallback_boundaries_shape():
    result = _fallback_boundaries('no api')
    assert result['confidence'] == 'fallback'
    assert result['op_start_s'] == 0.0
    assert result['op_end_s'] == float(oped_detect.FALLBACK_OP_SEC)
    assert result['ed_start_s'] is None
    assert 'no api' in result['warnings'][0]


# ═══════════════════════════════════════════════════════════════
# Prompt formatting
# ═══════════════════════════════════════════════════════════════

def test_format_cue_list_marks_empty_and_missing():
    assert _format_cue_list([]) == '  (无字幕行)\n'
    text = _format_cue_list([('00:00:01,000', ''), ('00:00:02,000', '歌')])
    assert '「空白」' in text and '歌' in text


def test_build_boundary_prompt_contains_episode_and_rules():
    episodes = [{'fname': 'EP001.srt', 'total_duration_s': 1440,
                 'head_cues': [('00:00:01,000', 'a')],
                 'tail_cues': [('00:23:00,000', 'b')]}]
    prompt = _build_boundary_prompt(episodes, lang='zh')
    assert 'EP001.srt' in prompt
    assert '（字幕为中文翻译）' in prompt
    assert 'JSON' in prompt


def test_extract_cues_for_boundary_samples_files(tmp_path):
    for i in range(1, 4):
        (tmp_path / f'EP{i:03d}.srt').write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n", encoding='utf-8')
    episodes = _extract_cues_for_boundary(str(tmp_path), sample_count=2)
    assert len(episodes) == 2
    assert all('head_cues' in ep for ep in episodes)

    assert _extract_cues_for_boundary(str(tmp_path / 'nope')) == []


# ═══════════════════════════════════════════════════════════════
# detect_boundaries
# ═══════════════════════════════════════════════════════════════

def test_detect_boundaries_without_files_falls_back(tmp_path):
    result = detect_boundaries(str(tmp_path))
    assert result['confidence'] == 'fallback'


def test_detect_boundaries_dry_run_skips_api(tmp_path, monkeypatch):
    (tmp_path / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n", encoding='utf-8')

    def boom(*a, **k):
        raise AssertionError('must not call API in dry-run')

    monkeypatch.setattr(oped_detect, '_call_llm', boom)
    result = detect_boundaries(str(tmp_path), dry_run=True)
    assert result['confidence'] == 'fallback'


def test_detect_boundaries_uses_llm_result(tmp_path, monkeypatch):
    (tmp_path / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n", encoding='utf-8')
    payload = json.dumps({
        'op': {'start_s': 0, 'end_s': 88},
        'ed': {'start_s': 1400, 'end_s': 1440},
        'preview': {'exists': False},
        'overall_confidence': 'high',
    })
    monkeypatch.setattr(oped_detect, '_call_llm', lambda *a, **k: payload)

    result = detect_boundaries(str(tmp_path), api_key='k')
    assert result['op_end_s'] == 88.0
    assert result['ed_start_s'] == 1400.0
    assert result['preview_end_s'] is None
    assert result['confidence'] == 'high'


def test_detect_boundaries_without_key_falls_back(tmp_path, monkeypatch):
    (tmp_path / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n", encoding='utf-8')
    monkeypatch.setattr(oped_detect, 'LLM_API_KEY', '')
    result = detect_boundaries(str(tmp_path))
    assert result['confidence'] == 'fallback'
    assert any('API key' in w for w in result['warnings'])
