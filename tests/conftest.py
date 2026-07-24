"""Shared pytest fixtures for subtitle-proofread tests."""

import json
import os
import pytest
from collections import OrderedDict
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# SRT fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def sample_srt_text():
    """A minimal valid SRT file with 3 cues in different languages."""
    return (
        "1\n00:00:01,000 --> 00:00:05,000\nHello world\n\n"
        "2\n00:00:06,000 --> 00:00:10,000\nこんにちは\n\n"
        "3\n00:00:11,000 --> 00:00:15,000\n你好世界\n\n"
    )


@pytest.fixture
def sample_srt_v2_text():
    """A variant SRT file for comparison/diff tests."""
    return (
        "1\n00:00:01,000 --> 00:00:05,000\nHello world fixed\n\n"
        "2\n00:00:06,000 --> 00:00:10,000\nこんにちは元気\n\n"
        "3\n00:00:11,000 --> 00:00:15,000\n你好世界修正\n\n"
    )


@pytest.fixture
def sample_cues():
    """Cue dictionaries matching sample_srt_text."""
    return [
        {'_srt_index': 1, 'start': '00:00:01,000', 'end': '00:00:05,000',
         'start_s': 1.0, 'end_s': 5.0, 'text': 'Hello world'},
        {'_srt_index': 2, 'start': '00:00:06,000', 'end': '00:00:10,000',
         'start_s': 6.0, 'end_s': 10.0, 'text': 'こんにちは'},
        {'_srt_index': 3, 'start': '00:00:11,000', 'end': '00:00:15,000',
         'start_s': 11.0, 'end_s': 15.0, 'text': '你好世界'},
    ]


# ═══════════════════════════════════════════════════════════════
# Report fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def sample_report_data():
    """Representative report data for update_report tests."""
    return OrderedDict([
        ('2', [
            {'ep': 'EP001', 'time': '00:00:01.000', 'original': 'bad_text',
             'corrected': 'good_text', 'status': '✅'},
            {'ep': 'EP001', 'time': '00:00:06.000', 'original': 'me',
             'corrected': '', 'status': '⬜'},
        ]),
        ('3', [
            {'ep': '', 'time': '', 'original': 'アトム',
             'corrected': '阿童木', 'status': '⬜'},
        ]),
    ])


@pytest.fixture
def temp_srt(tmp_path, sample_srt_text):
    """Write sample_srt_text to a temp .srt file, return its path."""
    p = tmp_path / "test.srt"
    p.write_text(sample_srt_text, encoding='utf-8')
    return str(p)


@pytest.fixture
def temp_srt_v2(tmp_path, sample_srt_v2_text):
    """Write sample_srt_v2_text to a temp .srt file, return its path."""
    p = tmp_path / "test_v2.srt"
    p.write_text(sample_srt_v2_text, encoding='utf-8')
    return str(p)


@pytest.fixture
def temp_report_md_path(tmp_path, sample_report_data):
    """Set up temp/report.json (authoritative store) and return the
    reports/问题解决报告.md path that update_report functions expect."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    json_path = temp_dir / "report.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'version': 1,
            'updated': '2026-01-01T00:00:00',
            'layers': sample_report_data,
        }, f, ensure_ascii=False, indent=2)
    # Return the MD path — read_report() derives JSON path from this
    return str(reports_dir / "问题解决报告.md")


# ═══════════════════════════════════════════════════════════════
# Environment isolation helpers
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def clean_input_dir_env():
    """Remove INPUT_DIR / SUBTITLE_INPUT_DIR from env for test isolation."""
    saved = {}
    for key in ('INPUT_DIR', 'SUBTITLE_INPUT_DIR'):
        saved[key] = os.environ.pop(key, None)
    yield
    for key, val in saved.items():
        if val is not None:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


# ═══════════════════════════════════════════════════════════════
# Tier 3 mock fixtures (used by test_fix_orchestrator etc.)
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_whisper_result():
    """A WhisperResult-like object for testing orchestrator logic."""
    from dataclasses import dataclass, field
    @dataclass
    class MockWhisperResult:
        source: str = 'whisper'
        tier: int = 1
        applied: int = 0
        ai_review: int = 0
        deleted: int = 0
        failed: int = 0
        auto_keep_fixes: list = field(default_factory=list)
        auto_cuts: list = field(default_factory=list)
        ai_fragments: list = field(default_factory=list)
        new_cues: list = field(default_factory=list)
        details: list = field(default_factory=list)
        placeholder_count: int = 0

    return MockWhisperResult(
        source='whisper', tier=1, applied=2,
        auto_keep_fixes=[
            {'start': '00:00:01.000', 'end': '00:00:05.000',
             'original': 'bad', 'replacement': 'good'},
            {'start': '00:00:06.000', 'end': '00:00:10.000',
             'original': 'bad2', 'replacement': 'good2'},
        ],
        auto_cuts=[
            {'start': '00:00:99.000', 'end': '00:01:00.000',
             'original': 'noise'},
        ],
    )
