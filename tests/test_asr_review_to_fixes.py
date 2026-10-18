"""Tests for converting ASR review decisions into apply_fixes actions."""

import json

from apply.asr_review_to_fixes import (
    convert_files, convert_worklist, main,
)


def _item(**kw):
    base = {'cue_index': 0, 'start': '00:00:01,000', 'decision': ''}
    base.update(kw)
    return base


def test_convert_worklist_actions_and_errors():
    data = {'srt': 'EP001.srt', 'items': [
        _item(cue_index=1, decision='fix_time',
              new_start='00:00:01,500', new_end='00:00:02,500'),
        _item(cue_index=2, decision='replace_text', replacement='早上好'),
        _item(cue_index=3, decision='delete'),
        _item(cue_index=4, decision='keep'),
        _item(cue_index=5, decision='split'),
    ]}
    fixes, errors = convert_worklist(data)
    assert [f['action'] for f in fixes] == ['set_time', 'replace_text', 'delete_line']
    assert fixes[0]['new_start'] == '00:00:01,500'
    assert fixes[0]['file'] == 'EP001.srt'
    assert fixes[1]['replacement'] == '早上好'
    assert len(errors) == 1 and 'split' in errors[0]


def test_convert_worklist_validation():
    fixes, errors = convert_worklist({'items': [
        _item(decision='fix_time'),
        _item(decision='replace_text'),
        _item(decision='delete'),
    ]}, default_srt='EP001.srt')
    assert [f['action'] for f in fixes] == ['delete_line']
    assert len(errors) == 2
    assert any('new_start' in e for e in errors)
    assert any('replacement' in e for e in errors)

    fixes, errors = convert_worklist({'items': [_item(decision='delete')]})
    assert fixes == []
    assert any('srt' in e for e in errors)


def test_convert_files_dedupes(tmp_path):
    p = tmp_path / 'EP001.asr_review.json'
    p.write_text(json.dumps({'srt': 'EP001.srt', 'items': [
        _item(decision='delete'), _item(decision='delete')]}), encoding='utf-8')
    fixes, errors = convert_files([str(p)])
    assert len(fixes) == 1
    assert not errors


def test_main_writes_fixes(tmp_path, capsys):
    scans = tmp_path / 'scans'
    scans.mkdir()
    (scans / 'EP001.asr_review.json').write_text(json.dumps({
        'srt': 'EP001.srt',
        'items': [_item(decision='replace_text', replacement='早上好')],
    }), encoding='utf-8')
    out = tmp_path / 'ai_review_fixes.json'
    assert main(['--scans-dir', str(scans), '--out', str(out)]) == 0
    data = json.loads(out.read_text(encoding='utf-8'))
    assert data[0]['action'] == 'replace_text'
    assert 'wrote' in capsys.readouterr().err
