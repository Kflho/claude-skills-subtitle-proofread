"""RU（俄语）源支持：提示词、残留判定、质量闸门。

Sally 项目暴露的三个缺口，各一条测试锁住：
  1. system prompt 对 ru 只说「把专有名词保留原文」→ 模型照抄西里尔字母
  2. `is_untranslated` 只比「译文 == 原文」→ 半句俄语+半句中文判成功
  3. `text_gate` 只 glob *.srt、只数拉丁 → ASS 项目与西里尔残留全漏
"""

import json

import pytest

import translate_srt as tr
from eval import text_gate


# ═══════════════════════════════════════════════════════════════
# Prompt
# ═══════════════════════════════════════════════════════════════

def test_ru_prompt_forbids_cyrillic_residue():
    prompt = tr._build_system_prompt('ru')
    assert '俄语字幕翻译' in prompt
    assert '西里尔' in prompt          # 明说不能留俄文字母
    assert '音译' in prompt


def test_ru_prompt_keeps_punctuation_and_line_break_rules():
    prompt = tr._build_system_prompt('ru')
    assert '全角标点' in prompt
    assert '空格代替逗号' in prompt
    assert '\\N' in prompt


def test_ja_prompt_unchanged_by_ru_rules():
    ja = tr._build_system_prompt('ja')
    assert '俄语' not in ja
    assert '西里尔' not in ja
    assert '敬语' in ja


def test_user_template_uses_source_language_label():
    msg = tr.USER_TEMPLATE.format(
        lang_name='俄语', context_before='(无)', targets='1. Привет',
        glossary='(无)')
    assert '俄语原文' in msg and '日文' not in msg


def test_translate_batch_sends_ru_prompt(monkeypatch):
    seen = {}

    def fake_llm(messages, api_key, model, base_url):
        seen['system'] = messages[0]['content']
        seen['user'] = messages[1]['content']
        return json.dumps([{'id': 1, 'translation': '你好'}],
                          ensure_ascii=False)

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    out = tr._translate_batch([{'text': 'Привет'}], 'k', 'm', 'u', '',
                              source_lang='ru')
    assert out == ['你好']
    assert '俄语' in seen['system']
    assert '俄语' in seen['user']


# ═══════════════════════════════════════════════════════════════
# 残留判定
# ═══════════════════════════════════════════════════════════════

def test_ru_echo_is_untranslated():
    assert tr.is_untranslated('Привет', 'Привет', source_lang='ru') is True


def test_ru_half_translated_line_is_caught():
    # 半句没翻：不是「等于原文」，旧判据会放过
    assert tr.is_untranslated('Привет, как дела?', '你好, как дела?',
                              source_lang='ru') is True


def test_ru_good_translation_passes():
    assert tr.is_untranslated('Привет', '你好', source_lang='ru') is False


def test_ru_rule_does_not_leak_into_ja():
    assert tr.is_untranslated('どうか', 'Привет') is False


def test_bad_ratio_counts_cyrillic_residue():
    cues = [{'text': 'Привет'}, {'text': 'Пока'}]
    result = ['你好', 'Пока']
    assert tr.bad_ratio(cues, result, source_lang='ru') == 0.5
    # 不传源语言时退回旧判据：'Пока' != 'Пока'? 相等 → 仍算回显
    assert tr.bad_ratio([{'text': 'Пока'}], ['Пока']) == 1.0


# ═══════════════════════════════════════════════════════════════
# 质量闸门
# ═══════════════════════════════════════════════════════════════

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


def _ass(path, texts):
    lines = [f'Dialogue: 0,0:00:{i * 2 + 1:02d}.00,0:00:{i * 2 + 2:02d}.00,'
             f'Default,,0,0,0,,{t}' for i, t in enumerate(texts)]
    path.write_text(ASS_HEAD + '\n'.join(lines) + '\n', encoding='utf-8')
    return str(path)


def test_gate_reads_ass_files(tmp_path, capsys):
    _ass(tmp_path / 'Mahou Tsukai Sally 001.ass', ['Привет', '你好'])
    rc = text_gate.main(['--zh-dir', str(tmp_path), '--max-cyrillic', '0'])
    err = capsys.readouterr().err
    assert rc == 1
    assert 'cyrillic 1' in err


def test_gate_counts_cjk_space(tmp_path, capsys):
    _ass(tmp_path / 'EP002.ass', ['妈妈又没空 爸爸很忙', '正常一句。'])
    rc = text_gate.main(['--zh-dir', str(tmp_path), '--max-cjk-space', '0'])
    assert rc == 1
    assert 'cjk_space 1' in capsys.readouterr().err


def test_gate_passes_clean_ru_translation(tmp_path, capsys):
    _ass(tmp_path / 'EP003.ass', ['我穿起来好看吗？', '有点太大了。'])
    rc = text_gate.main(['--zh-dir', str(tmp_path), '--max-cyrillic', '0',
                         '--max-cjk-space', '0', '--max-kana', '0'])
    assert rc == 0
    assert 'PASS' in capsys.readouterr().err


def test_gate_check_file_reports_new_counters():
    payload = text_gate.check_file
    assert payload is not None
    # 计数键必须存在，下游 report 才拿得到
    import inspect
    src = inspect.getsource(text_gate.check_file)
    assert "'cyrillic': cyrillic" in src
    assert "'cjk_space': cjk_space" in src


# ═══════════════════════════════════════════════════════════════
# ASS 换行标记：`\N` 不是合法 JSON 转义，会整批作废
# ═══════════════════════════════════════════════════════════════

def test_parse_accepts_ass_line_break_escape():
    raw = r'[{"id": 1, "translation": "礼物，\N亲手做的"},' \
          r' {"id": 2, "translation": "普通一句"}]'
    out = tr._parse_json_response(raw)
    assert out == ['礼物，\\N亲手做的', '普通一句']


def test_parse_accepts_ass_hard_space_escape():
    raw = r'[{"id": 1, "translation": "上\H下"}]'
    assert tr._parse_json_response(raw) == ['上\\H下']


def test_parse_keeps_already_escaped_line_break():
    raw = r'[{"id": 1, "translation": "礼物，\\N亲手做的"}]'
    assert tr._parse_json_response(raw) == ['礼物，\\N亲手做的']


def test_parse_leaves_normal_json_alone():
    raw = '[{"id": 1, "translation": "正常\\n换行"}]'
    assert tr._parse_json_response(raw) == ['正常\n换行']
