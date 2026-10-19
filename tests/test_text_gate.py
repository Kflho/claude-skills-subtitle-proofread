"""Text gate: fails a run with kana / echo / leftover variants / CPS."""

import json

import pytest

from eval import text_gate

GLOSSARY = {'サブ': '佐武', 'イチ': '市'}
META = {'aliases': {'佐武': ['サブヤン', '沙布'], '市': ['伊一']}}

CLEAN = ("1\n00:00:01,000 --> 00:00:03,000\n佐武来了\n\n"
         "2\n00:00:04,000 --> 00:00:06,000\n市在等你\n\n")
KANA = ("1\n00:00:01,000 --> 00:00:03,000\nサブ来了\n\n")
VARIANT = ("1\n00:00:01,000 --> 00:00:03,000\n沙布来了\n\n")


def _write(path, text):
    path.write_text(text, encoding='utf-8')
    return str(path)


def _glossary(tmp_path):
    g = tmp_path / 'g.json'
    g.write_text(json.dumps(GLOSSARY, ensure_ascii=False), encoding='utf-8')
    m = tmp_path / 'm.json'
    m.write_text(json.dumps(META, ensure_ascii=False), encoding='utf-8')
    return str(g), str(m)


def test_clean_translation_passes(tmp_path, capsys):
    _write(tmp_path / 'zh' / 'EP001.srt' if False else tmp_path / 'EP001.srt',
           CLEAN)
    rc = text_gate.main(['--zh-dir', str(tmp_path)])
    assert rc == 0
    assert 'PASS' in capsys.readouterr().err


def test_kana_residue_fails(tmp_path, capsys):
    _write(tmp_path / 'EP001.srt', KANA)
    rc = text_gate.main(['--zh-dir', str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert 'kana 1' in err and 'FAIL' in err


def test_kana_threshold_can_be_raised(tmp_path):
    _write(tmp_path / 'EP001.srt', KANA)
    assert text_gate.main(['--zh-dir', str(tmp_path), '--max-kana', '1']) == 0


def test_leftover_variant_fails_with_glossary(tmp_path, capsys):
    g, m = _glossary(tmp_path)
    _write(tmp_path / 'EP001.srt', VARIANT)
    rc = text_gate.main(['--zh-dir', str(tmp_path), '--glossary', g,
                         '--meta', m])
    assert rc == 1
    assert 'variants 1' in capsys.readouterr().err


def test_canonical_text_does_not_count_as_variant(tmp_path):
    g, m = _glossary(tmp_path)
    _write(tmp_path / 'EP001.srt', CLEAN)
    assert text_gate.main(['--zh-dir', str(tmp_path), '--glossary', g,
                           '--meta', m]) == 0


def test_echo_detected_only_with_source(tmp_path, capsys):
    # identical Latin line: an untranslated echo, and no kana to muddy the count
    line = ("1\n00:00:01,000 --> 00:00:03,000\nSabu ga kita\n\n")
    _write(tmp_path / 'EP001.srt', line)
    src_dir = tmp_path / 'ja'
    src_dir.mkdir()
    _write(src_dir / 'EP001.srt', line)

    assert text_gate.main(['--zh-dir', str(tmp_path)]) == 0    # no source → no echo
    rc = text_gate.main(['--zh-dir', str(tmp_path),
                         '--source-dir', str(src_dir)])
    assert rc == 1
    assert 'echo 1' in capsys.readouterr().err


def test_cps_is_soft_by_default(tmp_path):
    # 6 chars in a 0.2 s cue → 30 cps
    text = ("1\n00:00:01,000 --> 00:00:01,200\n这是一条很长的字幕\n\n")
    _write(tmp_path / 'EP001.srt', text)
    assert text_gate.main(['--zh-dir', str(tmp_path)]) == 0
    assert text_gate.main(['--zh-dir', str(tmp_path), '--max-cps', '0']) == 1


def test_report_contains_totals_and_items(tmp_path):
    _write(tmp_path / 'EP001.srt', KANA)
    report = tmp_path / 'gate.json'
    text_gate.main(['--zh-dir', str(tmp_path), '--report', str(report)])
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['totals']['kana'] == 1
    assert data['failures']
    assert data['files'][0]['items'][0]['cue'] == 1


def test_empty_directory_is_an_error(tmp_path, capsys):
    assert text_gate.main(['--zh-dir', str(tmp_path)]) == 2
    assert 'no subtitles' in capsys.readouterr().err

def test_latin_residue_is_counted_and_gated(tmp_path):
    import json
    from eval import text_gate

    zh = tmp_path / 'zh'
    zh.mkdir()
    (zh / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nMidori来了\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n字幕制作：TheSkaroHunting\n\n",
        encoding='utf-8')
    report = tmp_path / 'report.json'
    rc = text_gate.main(['--zh-dir', str(zh), '--max-latin', '0',
                         '--report', str(report)])
    data = json.loads(report.read_text(encoding='utf-8'))
    assert rc == 1
    assert data['totals']['latin'] == 1
    assert any('latin' in f for f in data['failures'])

    rc = text_gate.main(['--zh-dir', str(zh)])
    assert rc == 0
