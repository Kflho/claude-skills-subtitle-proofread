"""ASS 写回：样式、边距、时间码必须原样保留。

这两条是 Sally 项目实测踩出来的：
  · 新文件模式下所有 cue 被写成 Default，OP/招牌/标题的定位全丢
  · `_ass_time` 把 ASS 的百分秒当毫秒读，`.54` 写成 `.05`，整集轴往前缩
"""

import pytest

from lib.subtitle_io import _ass_time, read_subtitles, write_subtitles

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1024
PlayResY: 768

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.6,0.4,2,0,0,20,0
Style: Opening Rus,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H004C4C4C,0,0,0,0,100,100,0,0,1,1,0,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.06,0:00:03.20,Opening Rus,,10,10,10,,莎莉
Dialogue: 0,0:01:44.54,0:01:46.08,Default,,0,0,0,,你好
"""


def _write(path, text):
    path.write_text(text, encoding='utf-8')
    return str(path)


# ═══════════════════════════════════════════════════════════════
# 时间码
# ═══════════════════════════════════════════════════════════════

def test_ass_time_keeps_centiseconds():
    assert _ass_time('0:01:44.54') == '0:01:44.54'
    assert _ass_time('0:00:00.06') == '0:00:00.06'


def test_ass_time_converts_srt_milliseconds():
    assert _ass_time('00:01:44,540') == '0:01:44.54'
    assert _ass_time('00:00:01,000') == '0:00:01.00'
    assert _ass_time('00:00:00,054') == '0:00:00.05'


def test_ass_time_roundtrip_is_stable():
    for tc in ('0:00:01.00', '0:03:20.45', '1:02:03.99'):
        assert _ass_time(tc) == tc


# ═══════════════════════════════════════════════════════════════
# 写回
# ═══════════════════════════════════════════════════════════════

def test_write_preserves_styles_and_margins(tmp_path):
    src = _write(tmp_path / 'in.ass', ASS)
    cues = read_subtitles(src, mark_garbled=False)
    cues[0]['text'] = '莎莉来了'
    cues[1]['text'] = '你好啊'

    out = tmp_path / 'out.ass'
    write_subtitles(str(out), cues, template_path=src)

    rows = [l for l in out.read_text(encoding='utf-8').splitlines()
            if l.startswith('Dialogue:')]
    assert len(rows) == 2
    first = rows[0].split(',', 9)
    assert first[0] == 'Dialogue: 1'          # layer 保留
    assert first[3] == 'Opening Rus'          # 样式保留
    assert first[5:8] == ['10', '10', '10']   # 边距保留
    assert first[9] == '莎莉来了'
    assert rows[1].split(',')[3] == 'Default'


def test_write_preserves_timing_exactly(tmp_path):
    src = _write(tmp_path / 'in.ass', ASS)
    cues = read_subtitles(src, mark_garbled=False)
    out = tmp_path / 'out.ass'
    write_subtitles(str(out), cues, template_path=src)

    again = read_subtitles(str(out), mark_garbled=False)
    assert [c['start'] for c in again] == [c['start'] for c in cues]
    assert [round(c['start_s'], 2) for c in again] == \
           [round(c['start_s'], 2) for c in cues]


def test_write_falls_back_to_default_when_no_ass_source(tmp_path):
    src = _write(tmp_path / 'in.ass', ASS)
    cues = read_subtitles(src, mark_garbled=False)
    for cue in cues:
        cue.pop('_ass_dialogue', None)
    out = tmp_path / 'out.ass'
    write_subtitles(str(out), cues, template_path=src)
    rows = [l for l in out.read_text(encoding='utf-8').splitlines()
            if l.startswith('Dialogue:')]
    assert all(r.split(',')[3] == 'Default' for r in rows)


def test_write_recovers_from_header_only_output(tmp_path):
    """上次写盘失败留下只有表头的输出：必须重新写全，不能静默清空。"""
    src = _write(tmp_path / 'in.ass', ASS)
    cues = read_subtitles(src, mark_garbled=False)
    out = tmp_path / 'out.ass'
    header = ASS.split('[Events]')[0] + '[Events]\n' + ASS.split('[Events]')[1].splitlines()[1] + '\n'
    out.write_text(header, encoding='utf-8')      # 没有 Dialogue 行

    cues[0]['text'] = '莎莉来了'
    write_subtitles(str(out), cues, template_path=src)

    rows = [l for l in out.read_text(encoding='utf-8').splitlines()
            if l.startswith('Dialogue:')]
    assert len(rows) == 2
    assert '莎莉来了' in rows[0]


def test_write_switches_encoding_when_template_cannot_hold_chinese(tmp_path):
    """俄语源常见 cp1251：模板编码装不下中文时必须换 UTF-8，不能崩。"""
    src = tmp_path / 'in.ass'
    src.write_bytes(ASS.replace('莎莉', 'Салли')
                    .replace('你好', 'Привет').encode('cp1251'))
    cues = read_subtitles(str(src), mark_garbled=False)
    cues[0]['text'] = '莎莉来了'

    out = tmp_path / 'out.ass'
    write_subtitles(str(out), cues, template_path=str(src))

    raw = out.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')          # UTF-8 BOM
    assert '莎莉来了' in raw.decode('utf-8-sig')
    assert read_subtitles(str(out), mark_garbled=False)[0]['text'] == '莎莉来了'
