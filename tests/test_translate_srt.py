"""Tests for translate_srt.py — prompt building, glossary, OP/ED lyrics, LLM IO.

No network: urllib.request.urlopen is the only mocked seam.
"""

import json
import sys
from collections import Counter

import pytest

import translate_srt as tr


# ═══════════════════════════════════════════════════════════════
# Fixtures / helpers
# ═══════════════════════════════════════════════════════════════

def _srt(*texts):
    """Build a minimal SRT body with one cue per text (2s apart)."""
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n'
                      f'{text}\n')
    return '\n'.join(blocks)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ═══════════════════════════════════════════════════════════════
# Language detection & prompts
# ═══════════════════════════════════════════════════════════════

def test_detect_source_lang_by_script():
    assert tr._detect_source_lang([{'text': 'こんにちは'}]) == 'ja'
    assert tr._detect_source_lang([{'text': 'Привет'}]) == 'ru'
    assert tr._detect_source_lang([{'text': '你好世界'}]) == 'zh'
    assert tr._detect_source_lang([{'text': 'Hello'}]) == 'other'
    assert tr._detect_source_lang([]) == 'other'
    # plain strings are accepted too (legacy callers)
    assert tr._detect_source_lang(['こんにちは']) == 'ja'


def test_build_system_prompt_language_specific_rules():
    ja = tr._build_system_prompt('ja')
    assert '日语字幕翻译' in ja
    assert '省略冗余主语' in ja

    ru = tr._build_system_prompt('ru')
    assert '俄语字幕翻译' in ru
    assert '省略冗余主语' not in ru
    assert 'JSON' in ru


def test_glossary_for_batch_filters_absent_terms():
    mapping = {'アグモン': '亚古兽', 'タケル': '阿岳'}
    cues = [{'text': 'アグモン、行こう'}]
    assert tr._glossary_for_batch('', mapping, cues) == 'アグモン→亚古兽'
    assert tr._glossary_for_batch('', mapping, [{'text': 'おはよう'}]) == ''
    assert tr._glossary_for_batch('サリー', {}, cues) == 'サリー'


def test_build_system_prompt_hard_constraints():
    prompt = tr._build_system_prompt('ja', max_chars=20, max_cps=12)
    assert '每行最多 20 个字符' in prompt
    assert '12 字/秒' in prompt
    assert '硬约束' in prompt

    plain = tr._build_system_prompt('ja')
    assert '硬约束' not in plain


def test_write_qa_report(tmp_path):
    src = tmp_path / 'src.srt'
    out = tmp_path / 'out.srt'
    src.write_text(_srt('おはよう', 'アグモン'), encoding='utf-8')
    out.write_text(_srt('早上好', 'アグモン'), encoding='utf-8')
    report = tmp_path / 'qa.json'

    data = tr.write_qa_report(str(src), str(out), {'アグモン': '亚古兽'},
                              20, 12, str(report))

    assert report.exists()
    assert data['summary']['total'] == 2
    assert data['summary']['japanese_residual'] == 1
    assert data['summary']['glossary_miss'] == 1
    saved = json.loads(report.read_text(encoding='utf-8'))
    assert saved['summary']['echo'] == 1


# ═══════════════════════════════════════════════════════════════
# Glossary / mappings loading
# ═══════════════════════════════════════════════════════════════

def test_load_mappings_filters_metadata_and_empty_values(tmp_path):
    path = tmp_path / 'noun_mappings.json'
    path.write_text(json.dumps({
        '_comment': '整段说明文字不能进 prompt',
        'サリー': '莎莉',
        'アトム': '阿童木',
        '空': '',
    }, ensure_ascii=False), encoding='utf-8')

    glossary, mapping = tr.load_mappings(str(path))

    assert mapping == {'サリー': '莎莉', 'アトム': '阿童木'}
    assert glossary == 'アトム→阿童木, サリー→莎莉'
    assert '_comment' not in glossary


def test_load_mappings_accepts_utf8_bom(tmp_path):
    path = tmp_path / 'noun_mappings.json'
    path.write_text('\ufeff' + json.dumps({'サリー': '莎莉'}), encoding='utf-8')
    assert tr.load_mappings(str(path))[1] == {'サリー': '莎莉'}


def test_load_mappings_missing_file_is_empty():
    assert tr.load_mappings('no-such-file.json') == ('', {})


def test_load_glossary_skips_header_but_keeps_mapping(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text(
        '# 专有名词\n\n'
        '| 日语 | 中文 | 备注 |\n'
        '|------|------|------|\n'
        '| サリー | 莎莉 | 主角 |\n'
        '| アトム | 阿童木 | |\n', encoding='utf-8')

    glossary, mapping = tr.load_glossary(str(path))

    # 表头不能被当成一条 日语→中文 映射（踩坑 #17）
    assert mapping == {'サリー': '莎莉', 'アトム': '阿童木'}
    assert glossary == 'アトム→阿童木, サリー→莎莉'


def test_load_glossary_ignores_count_tables(tmp_path):
    """build_glossary 的 `日语|出现次数|变体` 表没有中文列，不能当映射。"""
    path = tmp_path / 'proper-nouns.md'
    path.write_text(
        '| 日语 | 出现次数 | 拼写变体 |\n'
        '|------|---------|---------|\n'
        '| サリー | 96 | サリーちゃん |\n', encoding='utf-8')

    assert tr.load_glossary(str(path)) == ('', {})


def test_load_glossary_single_column_term_list(tmp_path):
    path = tmp_path / 'proper-nouns.md'
    path.write_text('| サリー |\n| アトム |\n', encoding='utf-8')
    glossary, mapping = tr.load_glossary(str(path))
    assert glossary == 'アトム, サリー'
    assert mapping == {}


def test_load_glossary_missing_file_is_empty():
    assert tr.load_glossary('no-such-file.md') == ('', {})


# ═══════════════════════════════════════════════════════════════
# OP/ED detection & lyric-only replacement
# ═══════════════════════════════════════════════════════════════

def test_time_to_seconds_handles_srt_and_junk():
    assert tr._time_to_seconds('00:01:23,456') == pytest.approx(83.456)
    assert tr._time_to_seconds('1:00:00,000') == 3600
    assert tr._time_to_seconds('bad') == 0.0


def test_is_oped_region():
    assert tr._is_oped_region(0.0, 3.0, 1400.0) == 'OP'
    assert tr._is_oped_region(1300.0, 1399.0, 1400.0) == 'ED'
    assert tr._is_oped_region(700.0, 750.0, 1400.0) is None
    # no total duration → ED can't be decided, only OP
    assert tr._is_oped_region(700.0, 750.0, 0.0) is None


def test_pick_oped_canonical_uses_most_frequent():
    assert tr._pick_oped_canonical(Counter({'a': 1, 'b': 3})) == 'b'
    assert tr._pick_oped_canonical(Counter()) is None


def test_is_lyric_three_signals():
    canonical = '歌詞の一行'
    assert tr._is_lyric('歌詞の一行', canonical, Counter(), Counter()) is True
    assert tr._is_lyric('繰り返し', None, Counter({'繰り返し': 2}), Counter()) is True
    assert tr._is_lyric('主題歌', None, Counter(), Counter({'主題歌': 2})) is True
    # 窗口内只出现一次的台词不是歌词
    assert tr._is_lyric('正片の台詞', None, Counter({'正片の台詞': 1}),
                        Counter()) is False
    assert tr._is_lyric('   ', canonical, Counter(), Counter()) is False


def test_apply_oped_pre_replace_only_touches_lyrics():
    cues = [{'text': '歌詞の一行'}, {'text': '正片の台詞'},
            {'text': '歌詞の一行'}]
    oped = {
        'op_zh': '歌词一行',
        'op_texts': {'EP001.srt': [(0, '歌詞の一行'), (1, '正片の台詞'),
                                   (2, '歌詞の一行')]},
        'op_ja': '歌詞の一行',
        'op_variants': Counter({'歌詞の一行': 5, '正片の台詞': 1}),
    }

    assert tr.apply_oped_pre_replace(cues, 'EP001.srt', oped) == 2
    assert cues[0]['text'] == '歌词一行'
    assert cues[1]['text'] == '正片の台詞'      # 窗口内的正片对白不能覆写
    assert cues[2]['text'] == '歌词一行'


def test_apply_oped_pre_replace_skips_changed_text():
    cues = [{'text': 'サリー'}]
    oped = {
        'op_zh': '歌词一行',
        'op_texts': {'EP001.srt': [(0, '歌詞の一行')]},
        'op_ja': '歌詞の一行',
        'op_variants': Counter({'歌詞の一行': 3}),
    }
    assert tr.apply_oped_pre_replace(cues, 'EP001.srt', oped) == 0
    assert cues[0]['text'] == 'サリー'


def test_collect_oped_across_episodes_dry_run(tmp_path, capsys):
    (tmp_path / 'EP001.srt').write_text(
        _srt('歌詞の一行', '正片の台詞', '歌詞の一行')
        + '\n' + '4\n00:22:00,000 --> 00:22:05,000\nエンディング\n',
        encoding='utf-8')
    (tmp_path / 'EP002.srt').write_text(
        _srt('歌詞の一行', '別の台詞', '歌詞の一行'), encoding='utf-8')

    oped = tr.collect_oped_across_episodes(str(tmp_path), 'key', 'model',
                                           'http://x', dry_run=True)

    assert oped['op_ja'] == '歌詞の一行'
    assert oped['ed_ja'] == 'エンディング'
    assert set(oped['op_texts']) == {'EP001.srt', 'EP002.srt'}
    # dry-run: no LLM call, so no canonical Chinese
    assert oped['op_zh'] is None and oped['ed_zh'] is None
    assert oped['op_variants']['歌詞の一行'] == 4


# ═══════════════════════════════════════════════════════════════
# Proper-noun pre-replacement
# ═══════════════════════════════════════════════════════════════

def test_apply_noun_pre_replace_counts_and_rewrites():
    cues = [{'text': 'サリーが来た'}, {'text': ''},
            {'text': 'アトムとサリー'}]

    replaced = tr.apply_noun_pre_replace(
        cues, {'サリー': '莎莉', 'アトム': '阿童木'})

    assert replaced == 3
    assert cues[0]['text'] == '莎莉が来た'
    assert cues[1]['text'] == ''
    assert cues[2]['text'] == '阿童木と莎莉'


def test_apply_noun_pre_replace_without_mapping_is_noop():
    cues = [{'text': 'サリー'}]
    assert tr.apply_noun_pre_replace(cues, {}) == 0
    assert cues[0]['text'] == 'サリー'


# ═══════════════════════════════════════════════════════════════
# LLM IO
# ═══════════════════════════════════════════════════════════════

def test_call_llm_posts_chat_completion(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured['url'] = req.get_full_url()
        captured['headers'] = dict(req.header_items())
        captured['body'] = json.loads(req.data.decode('utf-8'))
        return _FakeResponse({'choices': [{'message': {'content': '["ok"]'}}]})

    monkeypatch.setattr(tr.urllib.request, 'urlopen', fake_urlopen)

    out = tr._call_llm([{'role': 'user', 'content': 'hi'}], 'sk-1', 'm',
                       'https://api.deepseek.com/v1')

    assert out == '["ok"]'
    assert captured['url'].endswith('/chat/completions')
    assert captured['headers']['Authorization'] == 'Bearer sk-1'
    assert captured['body']['model'] == 'm'
    assert captured['body']['messages'] == [{'role': 'user', 'content': 'hi'}]


def test_call_llm_warns_when_content_is_empty(monkeypatch, capsys):
    payload = {'choices': [{
        'message': {'content': '', 'reasoning_content': '思考过程'},
        'finish_reason': 'length',
    }]}
    monkeypatch.setattr(tr.urllib.request, 'urlopen',
                        lambda req, timeout=None: _FakeResponse(payload))

    assert tr._call_llm([], 'k', 'm', 'http://x') == ''
    err = capsys.readouterr().err
    assert '空 content' in err
    assert '思考 token' in err


def test_call_llm_returns_none_on_transport_error(monkeypatch, capsys):
    def boom(req, timeout=None):
        raise RuntimeError('network down')

    monkeypatch.setattr(tr.urllib.request, 'urlopen', boom)

    assert tr._call_llm([], 'k', 'm', 'http://x') is None
    assert 'API error' in capsys.readouterr().err


@pytest.mark.parametrize('response, expected', [
    ('["译一", "译二"]', ['译一', '译二']),
    ('```json\n["译一"]\n```', ['译一']),
    ('好的：\n["译一", "译二"]', ['译一', '译二']),
    ('[{"index": 1, "translation": "译一"}]', ['译一']),
    ('[{"zh": "译一"}]', ['译一']),
    ('["译一", "译二",]', ['译一', '译二']),       # trailing comma
    ('["译一", "译二"', ['译一', '译二']),          # truncated bracket
])
def test_parse_json_response_variants(response, expected):
    assert tr._parse_json_response(response) == expected


def test_parse_json_response_rejects_garbage(capsys):
    assert tr._parse_json_response('完全不是 JSON') is None
    assert tr._parse_json_response('') is None
    assert 'Failed to parse JSON' in capsys.readouterr().err


def test_normalize_batch_result_id_protocol():
    assert tr._normalize_batch_result(
        [{'id': 2, 'translation': '再见'},
         {'id': 1, 'translation': '你好'}], 2) == ['你好', '再见']
    assert tr._normalize_batch_result(
        [{'id': 1, 'translation': '你好'}], 2) is None
    assert tr._normalize_batch_result(['你好'], 1) == ['你好']
    assert tr._normalize_batch_result([{'id': 1, 'translation': ''}], 1) == [None]


def test_translate_batch_builds_prompt(monkeypatch):
    seen = {}

    def fake_llm(messages, api_key, model, base_url):
        seen.update(messages=messages, api_key=api_key)
        return '["你好", "再见"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)

    cues = [{'text': 'こんにちは'}, {'text': 'さようなら'}]
    out = tr._translate_batch(cues, 'key', 'model', 'http://x',
                              'サリー=莎莉',
                              ja_context=[{'text': '前の台詞'}],
                              source_lang='ja', context_limit=3)

    assert out == ['你好', '再见']
    system, user = seen['messages']
    assert system['role'] == 'system' and '日语' in system['content']
    assert '老年男性' not in system['content']
    assert user['role'] == 'user'
    assert '1. こんにちは' in user['content']
    assert '2. さようなら' in user['content']
    assert 'サリー=莎莉' in user['content']
    assert '前の台詞' in user['content']


def test_translate_batch_context_limit_none_keeps_all(monkeypatch):
    seen = {}

    def fake_llm(messages, api_key, model, base_url):
        seen.update(messages=messages)
        return '["你好"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    ctx = [{'text': f'前{i}'} for i in range(1, 6)]
    tr._translate_batch([{'text': 'こんにちは'}], 'k', 'm', 'u', '',
                        ja_context=ctx, source_lang='ja',
                        context_limit=None)
    user = seen['messages'][1]['content']
    assert all(f'前{i}' in user for i in range(1, 6))


def test_translate_batch_default_has_no_context(monkeypatch):
    seen = {}

    def fake_llm(messages, api_key, model, base_url):
        seen.update(messages=messages)
        return '["你好"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    tr._translate_batch([{'text': 'こんにちは'}], 'k', 'm', 'u', '',
                        ja_context=[{'text': '前の台詞'}], source_lang='ja')
    assert '前の台詞' not in seen['messages'][1]['content']


def test_translate_batch_context_limit_zero_omits_context(monkeypatch):
    seen = {}

    def fake_llm(messages, api_key, model, base_url):
        seen.update(messages=messages)
        return '["你好"]'

    monkeypatch.setattr(tr, '_call_llm', fake_llm)
    tr._translate_batch([{'text': 'こんにちは'}], 'k', 'm', 'u', '',
                        ja_context=[{'text': '前の台詞'}], source_lang='ja',
                        context_limit=0)
    user = seen['messages'][1]['content']
    assert '前の台詞' not in user


def test_translate_batch_returns_none_when_api_fails(monkeypatch):
    monkeypatch.setattr(tr, '_call_llm', lambda *a, **k: None)
    assert tr._translate_batch([{'text': 'あ'}], 'k', 'm', 'u', '') is None


# ═══════════════════════════════════════════════════════════════
# Echo detection / batch quality
# ═══════════════════════════════════════════════════════════════

def test_is_untranslated_detects_echo_with_numbering():
    assert tr.is_untranslated('どうか', 'どうか') is True
    assert tr.is_untranslated('どうか', '1. どうか') is True
    assert tr.is_untranslated('どうか', ' 2、どうか ') is True
    assert tr.is_untranslated('どうか', '请') is False
    assert tr.is_untranslated('どうか', '') is True
    assert tr.is_untranslated('どうか', None) is True


def test_bad_ratio():
    cues = [{'text': 'a'}, {'text': 'b'}]
    assert tr.bad_ratio(cues, None) == 1.0
    assert tr.bad_ratio([], None) == 1.0
    assert tr.bad_ratio(cues, ['A', 'B']) == 0.0
    assert tr.bad_ratio(cues, ['a', 'B']) == 0.5
    # shorter result: missing entries count as bad
    assert tr.bad_ratio(cues, ['A']) == 0.5


# ═══════════════════════════════════════════════════════════════
# translate_file — timeline preserved, echoes not accepted
# ═══════════════════════════════════════════════════════════════

def test_translate_file_writes_translation_and_keeps_timeline(
        monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('こんにちは', 'さようなら'), encoding='utf-8')
    out = tmp_path / 'zh' / 'EP001.srt'
    monkeypatch.setattr(tr, '_translate_batch',
                        lambda *a, **k: ['你好', '再见'])

    total, translated, failed = tr.translate_file(
        str(src), str(out), '', {}, 'key', 'm', 'http://x',
        dry_run=False, source_lang='ja')

    assert (total, translated, failed) == (2, 2, 0)
    text = out.read_text(encoding='utf-8')
    assert '00:00:02,000 --> 00:00:03,000' in text
    assert '00:00:04,000 --> 00:00:05,000' in text
    assert '你好' in text and '再见' in text


def test_translate_file_dry_run_makes_no_calls(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('こんにちは'), encoding='utf-8')
    out = tmp_path / 'zh.srt'
    calls = []
    monkeypatch.setattr(tr, '_translate_batch',
                        lambda *a, **k: calls.append(1) or ['你好'])

    assert tr.translate_file(str(src), str(out), '', {}, 'key', 'm',
                             'http://x', dry_run=True,
                             source_lang='ja') == (1, 0, 0)
    assert calls == []
    assert not out.exists()


def test_translate_file_rejects_echoed_batch(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('こんにちは', 'さようなら'), encoding='utf-8')
    out = tmp_path / 'zh.srt'
    calls = []
    # Echo with prompt numbering: "different" text, still untranslated.
    monkeypatch.setattr(tr, '_translate_batch',
                        lambda *a, **k: calls.append(1) or ['1. こんにちは',
                                                            '2. さようなら'])

    total, translated, failed = tr.translate_file(
        str(src), str(out), '', {}, 'key', 'm', 'http://x',
        dry_run=False, source_lang='ja')

    assert (total, translated, failed) == (2, 0, 2)
    assert len(calls) == 2          # retried once, still echoed
    assert not out.exists()         # nothing translated → no output


def test_translate_file_empty_subtitles(tmp_path, capsys):
    src = tmp_path / 'empty.srt'
    src.write_text('', encoding='utf-8')
    assert tr.translate_file(str(src), str(tmp_path / 'o.srt'), '', {},
                             'k', 'm', 'u', dry_run=False) == (0, 0, 0)
    assert '0 cues' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Directory mode / episode filtering
# ═══════════════════════════════════════════════════════════════

def test_filter_by_start_exact_token_keeps_tail():
    files = ['EP001.srt', 'EP002.srt', 'EP010.srt', 'SP01.srt']
    assert tr._filter_by_start(files, 'EP002') == [
        'EP002.srt', 'EP010.srt', 'SP01.srt']


def test_filter_by_start_without_exact_token_uses_kind_prefix():
    files = ['EP001.srt', 'EP002.srt', 'EP010.srt', 'SP01.srt']
    assert tr._filter_by_start(files, 'EP005') == ['EP010.srt']
    assert tr._filter_by_start(files, 'garbage') == files


def test_parse_episodes():
    assert tr._parse_episodes('EP001-EP003') == ['EP001', 'EP002', 'EP003']
    assert tr._parse_episodes('SP01,SP02') == ['SP01', 'SP02']
    assert tr._parse_episodes(None) is None


def test_translate_dir_filters_episodes(monkeypatch, tmp_path, capsys):
    in_dir = tmp_path / 'in'
    in_dir.mkdir()
    for name in ('EP001.srt', 'EP002.srt', 'SP01.srt'):
        (in_dir / name).write_text(_srt('こんにちは'), encoding='utf-8')

    tr.translate_dir(str(in_dir), str(tmp_path / 'out'), '', {},
                     'key', 'm', 'http://x', dry_run=True,
                     skip_oped=True, episodes=['EP002'])

    err = capsys.readouterr().err
    assert 'EP002.srt' in err
    assert 'EP001.srt' not in err
    assert 'SP01.srt' not in err


def test_translate_dir_requires_input_dir(tmp_path):
    with pytest.raises(SystemExit):
        tr.translate_dir(str(tmp_path / 'missing'), str(tmp_path / 'out'),
                         '', {}, 'k', 'm', 'u', dry_run=True)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_main_requires_api_key_without_dry_run(monkeypatch):
    monkeypatch.setattr(tr, 'LLM_API_KEY', '')
    monkeypatch.setattr(sys, 'argv', ['translate_srt.py',
                                      '--input', 'EP001.srt'])
    with pytest.raises(SystemExit) as exc:
        tr.main()
    assert exc.value.code == 1


def test_main_dry_run_writes_nothing(monkeypatch, tmp_path):
    src = tmp_path / 'EP001.srt'
    src.write_text(_srt('こんにちは'), encoding='utf-8')
    out = tmp_path / 'EP001.zh.srt'
    monkeypatch.setattr(tr, 'LLM_API_KEY', 'fake-key')
    monkeypatch.setattr(sys, 'argv', [
        'translate_srt.py', '--input', str(src), '--output', str(out),
        '--dry-run'])

    tr.main()

    assert not out.exists()
