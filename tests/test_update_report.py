"""Tests for utils/update_report.py — report data transforms + CRUD with JSON.

Phase 1: pure functions tested on in-memory data.
Phase 2 (Tier 2): CRUD functions tested with temp JSON files (tmp_path).
"""

import json
import os
import pytest
from collections import OrderedDict
from utils.update_report import (
    get_relevant_layers, get_layer_summary,
    _json_path, _parse_layer_header, _parse_table_row,
    _entry_key, _count_summary,
    read_report, write_report,
    upsert_entries, update_entry_status, delete_entry, replace_layer,
    LAYER_NAMES, STATUS_MAP, STATUS_PRIORITY,
)


# ═══════════════════════════════════════════════════════════════
# Pure function tests (no filesystem)
# ═══════════════════════════════════════════════════════════════

class TestGetRelevantLayers:
    def test_default(self):
        layers = get_relevant_layers()
        assert '2' in layers
        assert '2.5' in layers
        assert '3' in layers
        assert '4' in layers
        assert '6' in layers

    def test_default_excludes_ai_review(self):
        layers = get_relevant_layers()
        assert '3.5' not in layers  # no AI review by default

    def test_with_ai_review(self):
        layers = get_relevant_layers(has_ai_review=True)
        assert '3.5' in layers

    def test_ass_format(self):
        layers = get_relevant_layers(fmt='ass')
        # layer 5 may or may not be in LAYER_NAMES; just verify it's a valid call
        assert isinstance(layers, dict) or hasattr(layers, 'keys')

    def test_srt_format_no_layer5(self):
        layers = get_relevant_layers(fmt='srt')
        assert '5' not in layers


class TestJsonPath:
    def test_derives_temp_report_json(self):
        md_path = "/some/project/reports/问题解决报告.md"
        jp = _json_path(md_path)
        assert jp.endswith("report.json")
        assert "temp" in jp


class TestParseLayerHeader:
    def test_layer_2(self):
        assert _parse_layer_header('### Whisper 自动修复\n') == \
            ('2', 'Whisper 自动修复')

    def test_layer_2_5(self):
        assert _parse_layer_header('### AI 短碎片补全\n') == \
            ('2.5', 'AI 短碎片补全')

    def test_layer_6(self):
        assert _parse_layer_header('### 人工审查\n') == \
            ('6', '人工审查')

    def test_no_match(self):
        assert _parse_layer_header('random text') is None
        assert _parse_layer_header('') is None


class TestParseTableRow:
    def test_full_row(self):
        row = '| EP002 | 00:02:00.490 | me | 行くぞ | ✅ |'
        result = _parse_table_row(row)
        assert result is not None
        assert result['ep'] == 'EP002'
        assert result['time'] == '00:02:00.490'
        assert result['original'] == 'me'
        assert result['corrected'] == '行くぞ'
        assert result['status'] == '✅'

    def test_empty_status_defaults(self):
        row = '| EP002 | 00:02:00.490 | me |  |  |'
        result = _parse_table_row(row)
        assert result is not None
        assert result['status'] == '⬜'

    def test_skip_header(self):
        assert _parse_table_row('| 集数 | 时间 | 原错误字幕 | 整改后字幕 | 状态 |') is None

    def test_skip_separator(self):
        assert _parse_table_row('|------|------|-----------|-----------|:---:|') is None

    def test_non_table_line(self):
        assert _parse_table_row('just some text') is None


class TestEntryKey:
    def test_normal_entry(self):
        key = _entry_key({'ep': 'EP002', 'time': '00:02:00.490'})
        assert key == ('EP002', '00:02:00.490')

    def test_noun_entry(self):
        key = _entry_key({'ep': '', 'time': '', 'original': 'アトム'})
        assert key == ('__noun__', 'アトム')


class TestCountSummary:
    def test_mixed_statuses(self):
        data = {
            '2': [
                {'status': '✅'}, {'status': '✅'}, {'status': '✅'},
                {'status': '🗑️'}, {'status': '🗑️'},
                {'status': '⬜'},
            ]
        }
        fixed, pending, deleted = _count_summary(data)
        assert fixed == 3
        assert pending == 1
        assert deleted == 2

    def test_empty(self):
        fixed, pending, deleted = _count_summary({})
        assert fixed == 0
        assert pending == 0
        assert deleted == 0


class TestGetLayerSummary:
    def test_per_layer_counts(self):
        data = {
            '2': [{'status': '✅'}, {'status': '⬜'}, {'status': '🗑️'}],
            '3': [],
        }
        summary = get_layer_summary(data)
        assert summary['2']['fixed'] == 1
        assert summary['2']['pending'] == 1
        assert summary['2']['deleted'] == 1
        assert summary['2']['total'] == 3
        assert summary['3']['total'] == 0


class TestLayersConstants:
    def test_layer_names_ordered(self):
        keys = list(LAYER_NAMES.keys())
        assert keys == ['2', '2.5', '3', '3.5', '4', '6']

    def test_status_priority(self):
        assert STATUS_PRIORITY['✅'] > STATUS_PRIORITY['🗑️']
        assert STATUS_PRIORITY['🗑️'] > STATUS_PRIORITY['⬜']


# ═══════════════════════════════════════════════════════════════
# Tier 2: CRUD with temp JSON files
# ═══════════════════════════════════════════════════════════════

class TestReadWriteReport:
    def test_read_empty(self, temp_report_md_path):
        """read_report on a file with no matching JSON → returns {} or reads from JSON."""
        # temp_report_md_path sets up temp/report.json with sample_report_data
        data = read_report(temp_report_md_path)
        # Should have layer 2 from the fixture
        assert '2' in data
        assert len(data['2']) == 2

    def test_write_and_read_roundtrip(self, temp_report_md_path):
        data = read_report(temp_report_md_path)
        # Modify and write back
        data['6'] = [{
            'ep': 'EP001', 'time': '00:00:99.000',
            'original': 'new_issue', 'corrected': '', 'status': '⬜',
        }]
        write_report(temp_report_md_path, data)
        # Read back and verify
        data2 = read_report(temp_report_md_path)
        assert '6' in data2
        assert len(data2['6']) == 1
        assert data2['6'][0]['original'] == 'new_issue'


class TestUpsertEntries:
    def test_insert_new(self, temp_report_md_path):
        entries = [
            {'ep': 'EP002', 'time': '00:01:00.000', 'original': 'problem',
             'corrected': 'fixed', 'status': '✅'},
        ]
        upsert_entries(temp_report_md_path, step='2', entries=entries)
        data = read_report(temp_report_md_path)
        assert any(e['original'] == 'problem' for e in data.get('2', []))

    def test_upsert_no_downgrade(self, temp_report_md_path):
        """✅ should not be downgraded to ⬜."""
        # First write a ✅ entry
        upsert_entries(temp_report_md_path, step='2', entries=[
            {'ep': 'EP001', 'time': '00:00:01.000', 'original': 'original',
             'corrected': 'good', 'status': '✅'},
        ])
        # Try to overwrite with ⬜
        upsert_entries(temp_report_md_path, step='2', entries=[
            {'ep': 'EP001', 'time': '00:00:01.000', 'original': 'original',
             'corrected': '', 'status': '⬜'},
        ])
        data = read_report(temp_report_md_path)
        for e in data.get('2', []):
            if e['time'] == '00:00:01.000':
                assert e['status'] == '✅', 'Status should not be downgraded'


class TestUpdateEntryStatus:
    def test_update_found(self, temp_report_md_path):
        ok = update_entry_status(
            temp_report_md_path, step='2', ep='EP001',
            time='00:00:01.000', corrected='updated_text', status='✅',
        )
        assert ok is True
        data = read_report(temp_report_md_path)
        for e in data.get('2', []):
            if e['time'] == '00:00:01.000':
                assert e['corrected'] == 'updated_text'

    def test_update_not_found(self, temp_report_md_path):
        ok = update_entry_status(
            temp_report_md_path, step='2', ep='EP999',
            time='99:99:99.000', corrected='x', status='✅',
        )
        assert ok is False


class TestDeleteEntry:
    def test_delete_found(self, temp_report_md_path):
        # Add an entry first
        upsert_entries(temp_report_md_path, step='6', entries=[
            {'ep': 'EP001', 'time': '00:00:99.000', 'original': 'tmp',
             'corrected': '', 'status': '⬜'},
        ])
        ok = delete_entry(temp_report_md_path, step='6', ep='EP001',
                          time='00:00:99.000')
        assert ok is True
        data = read_report(temp_report_md_path)
        assert len(data.get('6', [])) == 0

    def test_delete_not_found(self, temp_report_md_path):
        ok = delete_entry(temp_report_md_path, step='6', ep='EP999',
                          time='99:99:99.000')
        assert ok is False


class TestReplaceLayer:
    def test_replace(self, temp_report_md_path):
        new_entries = [
            {'ep': '', 'time': '', 'original': '新専用名詞',
             'corrected': '新专有名词', 'status': '⬜'},
        ]
        replace_layer(temp_report_md_path, step='3', entries=new_entries)
        data = read_report(temp_report_md_path)
        assert len(data.get('3', [])) == 1
        assert data['3'][0]['original'] == '新専用名詞'

    def test_replace_with_empty(self, temp_report_md_path):
        replace_layer(temp_report_md_path, step='3', entries=[])
        data = read_report(temp_report_md_path)
        assert len(data.get('3', [])) == 0
