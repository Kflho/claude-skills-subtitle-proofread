"""Tests for region-based OP/ED lyric replacement (no audio, no API)."""

import io
import json
import os

import fix.oped_region as orr


def _srt(cues):
    out = []
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(f'{i}\n{start} --> {end}\n{text}\n')
    return '\n'.join(out)


def _write(path, text):
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _read(path):
    with io.open(path, encoding='utf-8-sig') as f:
        return f.read()


def test_spread_times_proportional_to_weights():
    times = orr.spread_times(10.0, 16.0, [1, 2, 3])
    assert [round(s, 2) for s, _ in times] == [10.0, 11.0, 13.0]
    assert times[-1][1] == 16.0
    # 每段时长 = 总长 × 权重占比
    assert round(times[1][1] - times[1][0], 2) == 2.0


def test_spread_times_rejects_empty():
    assert orr.spread_times(10.0, 16.0, []) == []
    assert orr.spread_times(16.0, 10.0, [1, 1]) == []


def test_rebuild_region_replaces_text_and_collapses_cues():
    cues = [
        {'start': '00:00:01,000', 'end': '00:00:02,000',
         'start_s': 1.0, 'end_s': 2.0, 'text': 'こんにちは'},
        {'start': '00:00:12,000', 'end': '00:00:13,000',
         'start_s': 12.0, 'end_s': 13.0, 'text': 'だれが集めた'},
        {'start': '00:00:14,000', 'end': '00:00:15,000',
         'start_s': 14.0, 'end_s': 15.0, 'text': '赤い熱い血'},
        {'start': '00:00:20,000', 'end': '00:00:21,000',
         'start_s': 20.0, 'end_s': 21.0, 'text': 'ありがとう'},
    ]
    out, removed, inserted = orr.rebuild_region(
        cues, {'start': 10.0, 'end': 18.0}, ['第一句', '第二句'])
    assert (removed, inserted) == (2, 2)
    texts = [c['text'] for c in out]
    assert texts == ['こんにちは', '第一句', '第二句', 'ありがとう']
    assert out[1]['start_s'] == 12.0 and out[2]['start_s'] == 13.5
    assert out[1]['end_s'] <= out[2]['start_s']
    starts = [c['start_s'] for c in out]
    assert starts == sorted(starts)


def test_rebuild_region_skips_when_no_cues_inside():
    cues = [{'start': '00:00:01,000', 'end': '00:00:02,000',
             'start_s': 1.0, 'end_s': 2.0, 'text': 'x'}]
    out, removed, inserted = orr.rebuild_region(
        cues, {'start': 100.0, 'end': 160.0}, ['a'])
    assert (removed, inserted) == (0, 0)
    assert out == cues


def test_cli_writes_copies_only(tmp_path):
    src = tmp_path / 'subs'
    src.mkdir()
    _write(src / 'EP001.srt', _srt([
        ('00:00:01,000', '00:00:02,000', '对白'),
        ('00:00:11,000', '00:00:12,000', 'だれが集めた'),
        ('00:00:13,000', '00:00:14,000', '赤い熱い血'),
        ('00:00:30,000', '00:00:31,000', '台词'),
    ]))
    windows = tmp_path / 'win.json'
    windows.write_text(json.dumps({'EP001': {'op': {'start': 10.0, 'end': 16.0}}}),
                       encoding='utf-8')
    lines = tmp_path / 'op.txt'
    lines.write_text('并非有谁召集\n赤热之血\n', encoding='utf-8')
    out = tmp_path / 'out'
    code = orr.main([str(src), '--windows', str(windows),
                     '--op-lines', str(lines), '--out-dir', str(out)])
    assert code == 0
    assert '原文' not in _read(src / 'EP001.srt')  # 源文件不动
    body = _read(out / 'EP001.srt')
    assert '并非有谁召集' in body and '赤热之血' in body
    assert 'だれが集めた' not in body


def test_cli_in_place_creates_bak(tmp_path):
    src = tmp_path / 'subs'
    src.mkdir()
    path = src / 'EP002.srt'
    _write(path, _srt([
        ('00:00:11,000', '00:00:12,000', 'たまい信号は変なやつ'),
        ('00:00:13,000', '00:00:14,000', 'おせんち'),
    ]))
    windows = tmp_path / 'win.json'
    windows.write_text(json.dumps({'EP002': {'ed': {'start': 10.0, 'end': 16.0}}}),
                       encoding='utf-8')
    lines = tmp_path / 'ed.txt'
    lines.write_text('玉井真吾是个怪家伙\n怕羞 开朗 又多愁善感\n', encoding='utf-8')
    code = orr.main([str(src), '--windows', str(windows),
                     '--ed-lines', str(lines), '--in-place'])
    assert code == 0
    assert (src / 'EP002.srt.bak').exists()
    body = _read(path)
    assert '玉井真吾是个怪家伙' in body
    assert 'たまい信号' not in body


def test_cli_dry_run_does_not_write(tmp_path):
    src = tmp_path / 'subs'
    src.mkdir()
    path = src / 'EP001.srt'
    original = _srt([('00:00:11,000', '00:00:12,000', 'x')])
    _write(path, original)
    windows = tmp_path / 'win.json'
    windows.write_text(json.dumps({'EP001': {'op': {'start': 10.0, 'end': 16.0}}}),
                       encoding='utf-8')
    lines = tmp_path / 'op.txt'
    lines.write_text('甲\n', encoding='utf-8')
    out = tmp_path / 'out'
    code = orr.main([str(src), '--windows', str(windows), '--op-lines',
                     str(lines), '--out-dir', str(out), '--dry-run'])
    assert code == 0
    assert _read(path) == original
    assert not os.path.exists(out / 'EP001.srt')
