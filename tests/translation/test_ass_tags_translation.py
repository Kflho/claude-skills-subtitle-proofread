"""ASS 覆盖标签必须绕过翻译，并在写盘时原样回来。

Sally 实测：83 条带 `{\\fad}`/`{\\pos}` 的 cue 送进模型后，大多数标签被删，
标题和招牌因此掉到默认位置。标签不是文本，不该经过翻译。
"""

import translate_srt as tr
from lib.subtitle_io import read_subtitles

ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1024
PlayResY: 768

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.6,0.4,2,0,0,20,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass(path, rows):
    body = '\n'.join(f'Dialogue: 0,{s},{e},Default,,0,0,0,,{t}' for s, e, t in rows)
    path.write_text(ASS_HEAD + body + '\n', encoding='utf-8')
    return str(path)


# ═══════════════════════════════════════════════════════════════
# split_ass_tags
# ═══════════════════════════════════════════════════════════════

def test_split_prefix_tags():
    prefix, visible, suffix = tr.split_ass_tags(r'{\fad(100,100)}你好')
    assert prefix == r'{\fad(100,100)}'
    assert visible == '你好'
    assert suffix == ''


def test_split_prefix_and_suffix_tags():
    prefix, visible, suffix = tr.split_ass_tags(r'{\pos(1,2)}正文{\r}')
    assert prefix == r'{\pos(1,2)}'
    assert visible == '正文'
    assert suffix == r'{\r}'


def test_split_no_tags():
    assert tr.split_ass_tags('普通一句') == ('', '普通一句', '')


def test_split_empty_text():
    assert tr.split_ass_tags('') == ('', '', '')


# ═══════════════════════════════════════════════════════════════
# 翻译流程
# ═══════════════════════════════════════════════════════════════

def test_tags_are_not_sent_to_model(tmp_path, monkeypatch):
    path = _ass(tmp_path / 'EP001.ass', [
        ('0:00:01.00', '0:00:03.00', r'{\fad(500,200)\an8}Появление'),
        ('0:00:04.00', '0:00:06.00', '普通一句'),
    ])
    sent = []

    def fake_batch(cues, *args, **kwargs):
        sent.extend(c['text'] for c in cues)
        return ['出现' if 'Появление' in c['text'] else '普通译文'
                for c in cues]

    monkeypatch.setattr(tr, '_translate_batch', fake_batch)
    out = tmp_path / 'out.ass'
    tr.translate_file(str(path), str(out), '', {}, 'k', 'm', 'u',
                      source_lang='ru')

    assert sent == ['Появление', '普通一句']       # 标签不出现
    texts = [c['text'] for c in read_subtitles(str(out), mark_garbled=False)]
    assert texts[0] == r'{\fad(500,200)\an8}出现'   # 标签原样回来
    assert texts[1] == '普通译文'


def test_tags_survive_oped_pre_replace():
    cues = [{'start_s': 0.0,
             'text': r'{\fad(100,100)}Волшебное мамбо'}]
    oped = {
        'op_map': {'Волшебное мамбо': '魔法曼波'},
        'ed_map': {}, 'op_alias': {}, 'ed_alias': {},
        'op_texts': {'EP001.ass': [(0, 'Волшебное мамбо')]},
        'ed_texts': {}, 'op_zh': '魔法曼波', 'ed_zh': None,
        'op_src': 'Волшебное мамбо', 'ed_src': None,
        'op_variants': {}, 'ed_variants': {},
    }
    replaced = tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    assert replaced == 1
    assert cues[0]['text'] == r'{\fad(100,100)}魔法曼波'
