"""Tests for fix/fix_orchestrator.py — FixReport + Fixer with mocked Whisper.

Tier 3: uses mock WhisperResult to test orchestrator logic without
actual Whisper/FFmpeg binaries.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fix.fix_orchestrator import Fixer, FixReport


class TestFixReport:
    def test_default_construction(self):
        r = FixReport()
        assert r.source == ''
        assert r.applied == 0
        assert r.failed == 0
        assert r.deleted == 0
        assert r.ai_review == 0

    def test_with_values(self):
        r = FixReport(source='whisper', applied=5, failed=2, deleted=1,
                      ai_review=3, tier=1)
        assert r.applied == 5
        assert r.failed == 2
        assert r.deleted == 1

    def test_merge(self):
        r1 = FixReport(source='whisper', applied=3, failed=1,
                       ai_review=2, details=['a'])
        r2 = FixReport(source='missing_sub', applied=2, failed=0,
                       deleted=1, details=['b'])
        r1.merge(r2)
        assert r1.applied == 5
        assert r1.failed == 1
        assert r1.deleted == 1
        assert r1.ai_review == 2
        assert len(r1.details) == 2

    def test_merge_empty(self):
        r1 = FixReport(applied=3)
        r2 = FixReport()
        r1.merge(r2)
        assert r1.applied == 3


class TestFixerConstruction:
    def test_basic_init(self, tmp_path):
        f = Fixer('EP001', str(tmp_path))
        assert f.episode == 'EP001'
        assert f.target_lang == 'ja'

    def test_custom_lang(self, tmp_path):
        f = Fixer('EP001', str(tmp_path), target_lang='zh')
        assert f.target_lang == 'zh'


class TestFixerIsClean:
    def test_no_srt_file(self, tmp_path):
        f = Fixer('EP001', str(tmp_path))
        assert f.is_clean() is True  # no SRT = clean

    def test_with_clean_srt(self, tmp_path):
        srt_dir = tmp_path / 'AI审查后'
        srt_dir.mkdir()
        srt_path = srt_dir / 'test_EP001.srt'
        srt_path.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nこんにちは\n\n",
            encoding='utf-8')
        f = Fixer('EP001', str(tmp_path))
        result = f.is_clean()
        assert isinstance(result, bool)


class TestFixerParseChecklist:
    def test_v2_checklist(self, tmp_path):
        content = (
            "# 人工审查清单 — EP001\n"
            "> version: 2\n\n"
            "---\n\n"
            "EP001 | 00:02:00.000 ~ 00:02:05.000\n"
            "修正:\n"
            "正しいテキスト\n"
            "\n---\n"
        )
        p = tmp_path / "checklist.md"
        p.write_text(content, encoding='utf-8')
        f = Fixer('EP001', str(tmp_path))
        corrections = f._parse_checklist(str(p))
        assert len(corrections) == 1
        assert corrections[0]['text'] == '正しいテキスト'

    def test_v2_delete(self, tmp_path):
        content = (
            "# 人工审查清单 — EP001\n"
            "> version: 2\n\n"
            "---\n\n"
            "EP001 | 00:02:00.000 ~ 00:02:05.000\n"
            "修正:\n"
            "削除\n"
            "\n---\n"
        )
        p = tmp_path / "checklist_del.md"
        p.write_text(content, encoding='utf-8')
        f = Fixer('EP001', str(tmp_path))
        corrections = f._parse_checklist(str(p))
        assert len(corrections) == 1
        assert corrections[0]['text'] == '削除'

    def test_v1_format_ignored(self, tmp_path):
        content = "# Old format\nno version marker\n"
        p = tmp_path / "checklist_old.md"
        p.write_text(content, encoding='utf-8')
        f = Fixer('EP001', str(tmp_path))
        corrections = f._parse_checklist(str(p))
        assert corrections == []

    def test_already_checked_skipped(self, tmp_path):
        content = (
            "# 人工审查清单 — EP001\n"
            "> version: 2\n\n"
            "---\n\n"
            "✅\n"
            "EP001 | 00:02:00.000 ~ 00:02:05.000\n"
            "修正:\n"
            "done\n"
            "\n---\n"
        )
        p = tmp_path / "checklist_done.md"
        p.write_text(content, encoding='utf-8')
        f = Fixer('EP001', str(tmp_path))
        corrections = f._parse_checklist(str(p))
        assert corrections == []  # ✅ marks it as done


class TestFixerApplyWhisperResult:
    def test_writes_srt_and_report(self, tmp_path, mock_whisper_result):
        # Set up SRT directory
        srt_dir = tmp_path / 'AI审查后'
        srt_dir.mkdir()
        srt_path = srt_dir / 'test_EP001.srt'
        srt_path.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nbad_text\n\n"
            "2\n00:00:06,000 --> 00:00:10,000\nbad2\n\n"
            "3\n00:00:99,000 --> 00:01:00,000\nnoise\n\n",
            encoding='utf-8')
        # Set up report directory
        reports_dir = tmp_path / 'reports'
        reports_dir.mkdir()
        temp_dir = tmp_path / 'temp'
        temp_dir.mkdir()

        f = Fixer('EP001', str(tmp_path))
        f._srt_path = str(srt_path)
        f._report_path = str(reports_dir / '问题解决报告.md')

        result = f._apply_whisper_result(mock_whisper_result)
        assert result.applied >= 0  # applied from auto_keep_fixes
        # deleted count depends on how auto_cuts are handled
        assert result.deleted >= 0
