"""Tests for glossary evidence validation (no LLM)."""

from nouns.validate_glossary import validate


ASS_HEADER = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def test_validate_keeps_cooccurring_terms(tmp_path):
    ja = tmp_path / 'ja'
    zh = tmp_path / 'zh'
    ja.mkdir()
    zh.mkdir()
    (ja / 'Show.S01E01.ja[cc].srt').write_text(
        '1\n00:00:01,000 --> 00:00:02,000\nアグモン\n\n'
        '2\n00:00:02,000 --> 00:00:03,000\nタケル\n', encoding='utf-8')
    (zh / '01、x.sc.ass').write_text(
        ASS_HEADER +
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,亚古兽\n'
        'Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,阿岳\n',
        encoding='utf-8')

    result = validate(str(ja), str(zh), {
        'アグモン': '亚古兽', 'タケル': '阿岳', 'デジモン': '数码宝贝'})

    assert result['mappings'] == {'アグモン': '亚古兽', 'タケル': '阿岳'}
    assert 'デジモン' in result['dropped']
    assert result['episodes_paired'] == 1
    assert result['cue_pairs'] == 2
