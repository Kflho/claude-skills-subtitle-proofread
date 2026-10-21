"""OP/ED 段匹配：逐句歌词表，而不是「一句覆写整段」。

Sally 的俄语源有两个特征，正好把旧实现的假设打穿：
  · OP 歌词块在文件**末尾**，但时间戳是 0:00–1:04（按时间判区域才对）
  · 一个 OP 有 30 多条**各不相同**的歌词，旧实现只翻「出现最多的那句」，
    再把这个时间窗里的每一句都覆写成同一句中文

另外字幕文件常有 OP 与 ED 两块（同一首歌在片头和片尾各一次），
逐句表按文本匹配，天然跨集、跨首尾一致。
"""

import pytest

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

OP_LINES = [
    'Маха-рику маха-рита!',
    'Она прилетела из волшебной страны',
    'Как прекрасна эта девочка',
]
ED_LINES = [
    'Волшебное мамбо',
    'Волшебная страна - хороша!',
]


def _tc(seconds):
    return f'0:{int(seconds) // 60:02d}:{int(seconds) % 60:02d}.00'


def _write_ass(path, cues):
    """cues: [(start_s, end_s, text[, style])]，按列表顺序写进文件。"""
    rows = []
    for cue in cues:
        s, e, text = cue[0], cue[1], cue[2]
        style = cue[3] if len(cue) > 3 else 'Default'
        rows.append(f'Dialogue: 0,{_tc(s)},{_tc(e)},{style},,0,0,0,,{text}')
    path.write_text(ASS_HEAD + '\n'.join(rows) + '\n', encoding='utf-8')
    return str(path)


def _episode(path, body_lines, op=True, ed=True):
    """正片对白 + 末尾追加 OP 块（时间戳 0:00）+ 结尾 ED 块。

    歌词用 ASS 样式 Opening Rus 标出来——真实源里就是这样区分的，
    而正片开场对白（EP001 从 1:44 起）落在 180s 窗口内，不能当歌词。
    """
    cues = [(100 + i * 3, 102 + i * 3, t, 'Default')
            for i, t in enumerate(body_lines)]
    if op:
        cues += [(i * 4, i * 4 + 3.5, t, 'Opening Rus')
                 for i, t in enumerate(OP_LINES)]
    if ed:
        cues += [(1460 + i * 4, 1463 + i * 4, t, 'Opening Rus')
                 for i, t in enumerate(ED_LINES)]
        cues.append((1490, 1500, 'Конец', 'Default'))   # 真实片长
    return _write_ass(path, cues)


@pytest.fixture
def fake_translate(monkeypatch):
    """把 LLM 换成本地假翻译：每个源句一个固定的中文标签。

    不能回 `'中:' + 原文`——译文带西里尔会被 `is_untranslated` 判成没翻，
    逐句表直接拒收（那正是产品代码该做的事）。
    """
    labels = {}

    def fake_batch(cues, *args, **kwargs):
        out = []
        for cue in cues:
            text = cue['text']
            if text not in labels:
                labels[text] = f'译文{len(labels)}'
            out.append(labels[text])
        return out
    monkeypatch.setattr(tr, '_translate_batch', fake_batch)
    return fake_batch


def _collect(tmp_path, **kwargs):
    return tr.collect_oped_across_episodes(str(tmp_path), 'k', 'm', 'u',
                                           source_lang='ru', **kwargs)


# ═══════════════════════════════════════════════════════════════
# 收集层
# ═══════════════════════════════════════════════════════════════

def test_each_distinct_lyric_line_gets_its_own_translation(tmp_path, fake_translate):
    _episode(tmp_path / 'Mahou Tsukai Sally 001.ass', ['Привет', 'Пока'])
    oped = _collect(tmp_path)
    assert set(oped['op_map']) == set(OP_LINES)
    assert set(oped['ed_map']) == set(ED_LINES)
    assert oped['op_map'][OP_LINES[0]].startswith('译文')


def test_op_block_at_file_end_is_detected_by_timestamp(tmp_path, fake_translate):
    """OP 块在文件末尾不代表它是 ED —— 时间戳 0:00 才是判据。"""
    _episode(tmp_path / 'EP001.ass', ['Привет'], op=True, ed=True)
    oped = _collect(tmp_path)
    positions = oped['op_texts']['EP001.ass']
    assert len(positions) == len(OP_LINES)
    # 索引必须指向时间戳在开头的那些 cue，而不是文件末尾那批
    cues = read_subtitles(str(tmp_path / 'EP001.ass'), mark_garbled=False)
    for idx, text in positions:
        assert cues[idx]['start_s'] < 180
        assert text in OP_LINES


def test_ed_block_uses_total_duration_not_fixed_offset(tmp_path, fake_translate):
    _episode(tmp_path / 'EP001.ass', ['Привет'])
    oped = _collect(tmp_path)
    cues = read_subtitles(str(tmp_path / 'EP001.ass'), mark_garbled=False)
    for idx, text in oped['ed_texts']['EP001.ass']:
        assert cues[idx]['start_s'] > 1200
        assert text in ED_LINES


def test_line_map_is_shared_across_episodes(tmp_path, fake_translate):
    _episode(tmp_path / 'Mahou Tsukai Sally 001.ass', ['Привет'])
    _episode(tmp_path / 'Mahou Tsukai Sally 002.ass', ['Пока'])
    oped = _collect(tmp_path)
    assert len(oped['op_map']) == len(OP_LINES)   # 不是 2 倍
    assert len(oped['op_texts']) == 2


def test_short_noise_lines_are_not_translated(tmp_path, fake_translate):
    _write_ass(tmp_path / 'EP001.ass', [(0, 2, 'А'), (200, 202, 'Привет')])
    _collect(tmp_path)
    # 'А' 只有 1 个字符，不进表、不浪费调用


def test_dry_run_builds_structure_without_calling_llm(tmp_path, monkeypatch):
    called = []

    def boom(*args, **kwargs):
        called.append(1)
        return None

    monkeypatch.setattr(tr, '_translate_batch', boom)
    _episode(tmp_path / 'EP001.ass', ['Привет'])
    oped = _collect(tmp_path, dry_run=True)
    assert not called
    assert not oped['op_map']
    assert len(oped['op_texts']['EP001.ass']) == len(OP_LINES)


# ═══════════════════════════════════════════════════════════════
# 应用层
# ═══════════════════════════════════════════════════════════════

def test_apply_writes_each_line_not_one_collapsed_sentence(tmp_path, fake_translate):
    path = _episode(tmp_path / 'EP001.ass', ['Привет'])
    oped = _collect(tmp_path)
    cues = read_subtitles(path, mark_garbled=False)
    replaced = tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)

    op_values = set(oped['op_map'].values())
    op_texts = [c['text'] for c in cues if c['text'] in op_values]
    assert replaced >= len(OP_LINES) + len(ED_LINES)
    assert len(op_values) == len(OP_LINES)       # 三句各自有译文
    assert len(op_texts) == len(OP_LINES)        # 没有被压成一句


def test_apply_leaves_body_dialogue_untouched(tmp_path, fake_translate):
    path = _episode(tmp_path / 'EP001.ass', ['Привет', 'Пока'])
    oped = _collect(tmp_path)
    cues = read_subtitles(path, mark_garbled=False)
    tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    body = [c['text'] for c in cues if 100 <= c['start_s'] < 110]
    assert body == ['Привет', 'Пока']


def test_apply_is_idempotent(tmp_path, fake_translate):
    path = _episode(tmp_path / 'EP001.ass', ['Привет'])
    oped = _collect(tmp_path)
    cues = read_subtitles(path, mark_garbled=False)
    tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    again = tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    assert again == 0   # 已替换的文本对不上原文，第二轮不重复计数


# ═══════════════════════════════════════════════════════════════
# 回退：没有逐句表时走 canonical（Whisper 幻觉刷屏的老数据）
# ═══════════════════════════════════════════════════════════════

def test_fallback_canonical_only_replaces_repeated_lyric():
    cues = [
        {'start_s': 10, 'text': 'Волшебное мамбо'},
        {'start_s': 14, 'text': 'Волшебное мамбо'},
        {'start_s': 18, 'text': 'случайная реплика'},
    ]
    oped = {
        'op_map': {}, 'ed_map': {},
        'op_texts': {'EP001.ass': [(0, 'Волшебное мамбо'),
                                   (1, 'Волшебное мамбо'),
                                   (2, 'случайная реплика')]},
        'ed_texts': {},
        'op_zh': '魔法曼波', 'ed_zh': None,
        'op_src': 'Волшебное мамбо', 'ed_src': None,
        'op_variants': {'Волшебное мамбо': 2,
                        'случайная реплика': 1},
        'ed_variants': {},
    }
    replaced = tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    assert replaced == 2
    assert cues[0]['text'] == '魔法曼波'
    assert cues[2]['text'] == 'случайная реплика'   # 单次对白不能被歌词顶掉


def test_no_map_and_no_canonical_is_noop():
    cues = [{'start_s': 10, 'text': 'Волшебное мамбо'}]
    oped = {'op_map': {}, 'ed_map': {}, 'op_texts': {}, 'ed_texts': {},
            'op_zh': None, 'ed_zh': None, 'op_src': None, 'ed_src': None,
            'op_variants': {}, 'ed_variants': {}}
    assert tr.apply_oped_pre_replace(cues, 'EP001.ass', oped) == 0
    assert cues[0]['text'] == 'Волшебное мамбо'


# ═══════════════════════════════════════════════════════════════
# 双轨歌词：罗马音 + 俄语同一时间点
# ═══════════════════════════════════════════════════════════════

ROMAJI = ['Maha-riku, maha-rita!', 'Kuni kara yattekita',
          'Chotto charmu-na onna no ko']


def test_missing_lyric_line_is_left_for_normal_translation():
    """逐句表里没有的歌词不能被「窗口里最多的那句」顶替。"""
    cues = [{'text': 'Ещё одна строка'}]
    oped = {
        'op_map': {'другая строка': '别的句子'},
        'ed_map': {}, 'op_alias': {}, 'ed_alias': {},
        'op_texts': {'EP001.ass': [(0, 'Ещё одна строка')]},
        'ed_texts': {}, 'op_zh': '喂，别得意忘形！', 'ed_zh': None,
        'op_src': 'Волшебное мамбо', 'ed_src': None,
        'op_variants': {'Ещё одна строка': 99}, 'ed_variants': {},
    }
    replaced = tr.apply_oped_pre_replace(cues, 'EP001.ass', oped)
    assert replaced == 0
    assert cues[0]['text'] == 'Ещё одна строка'


def test_dual_track_translates_source_track_only(tmp_path, fake_translate):
    cues = []
    for i, (romaji, rus) in enumerate(zip(ROMAJI, OP_LINES)):
        cues.append((i * 4, i * 4 + 3.5, romaji, 'Opening Romaji'))
        cues.append((i * 4, i * 4 + 3.5, rus, 'Opening Rus'))
    cues.append((200, 202, 'Привет', 'Default'))
    _write_ass(tmp_path / 'Mahou Tsukai Sally 001.ass', cues)

    oped = _collect(tmp_path)
    # 只翻俄语那条；罗马音不进表、不单独花调用
    assert set(oped['op_map']) == set(OP_LINES)
    assert set(oped['op_alias']) == set(ROMAJI)


def test_dual_track_romaji_reuses_russian_translation(tmp_path, fake_translate):
    cues = []
    for i, (romaji, rus) in enumerate(zip(ROMAJI, OP_LINES)):
        cues.append((i * 4, i * 4 + 3.5, romaji, 'Opening Romaji'))
        cues.append((i * 4, i * 4 + 3.5, rus, 'Opening Rus'))
    path = _write_ass(tmp_path / 'Mahou Tsukai Sally 001.ass', cues)
    oped = _collect(tmp_path)

    parsed = read_subtitles(path, mark_garbled=False)
    tr.apply_oped_pre_replace(parsed, 'Mahou Tsukai Sally 001.ass', oped)

    by_start = {}
    for cue in parsed:
        by_start.setdefault(round(cue['start_s'], 2), []).append(cue['text'])
    for start, texts in by_start.items():
        assert len(texts) == 2
        assert texts[0] == texts[1]            # 两条轨措辞一致
        assert texts[0].startswith('译文')      # 用的是俄语轨的译文


# ═══════════════════════════════════════════════════════════════
# 预替换过的条目不能再送翻译（否则被当成「未翻译」白烧调用）
# ═══════════════════════════════════════════════════════════════

def test_oped_cues_are_not_sent_to_translation(tmp_path, monkeypatch):
    path = _episode(tmp_path / 'Mahou Tsukai Sally 001.ass', ['Привет'])
    sent = []

    def fake_batch(cues, *args, **kwargs):
        sent.extend(c['text'] for c in cues)
        return [f'译文{i}' for i in range(len(cues))]

    monkeypatch.setattr(tr, '_translate_batch', fake_batch)
    oped = {
        'op_map': {OP_LINES[0]: '魔法咒语第一句'},
        'ed_map': {},
        'op_alias': {}, 'ed_alias': {},
        'op_texts': {'Mahou Tsukai Sally 001.ass': [(1, OP_LINES[0])]},
        'ed_texts': {},
        'op_zh': '魔法咒语第一句', 'ed_zh': None,
        'op_src': OP_LINES[0], 'ed_src': None,
        'op_variants': {}, 'ed_variants': {},
    }
    out = tmp_path / 'out.ass'
    tr.translate_file(str(path), str(out), '', {}, 'k', 'm', 'u',
                      oped=oped, source_lang='ru')

    assert '魔法咒语第一句' not in sent          # 预替换过的没进模型
    assert OP_LINES[0] not in sent
    result = read_subtitles(str(out), mark_garbled=False)
    assert any(c['text'] == '魔法咒语第一句' for c in result)
    assert any(c['text'].startswith('译文') for c in result)


def test_real_newline_in_translation_becomes_ass_marker(tmp_path, monkeypatch):
    path = _episode(tmp_path / 'EP001.ass', ['Привет'], op=False, ed=False)

    def fake_batch(cues, *args, **kwargs):
        return ['第一行\n第二行' for _ in cues]

    monkeypatch.setattr(tr, '_translate_batch', fake_batch)
    out = tmp_path / 'out.ass'
    tr.translate_file(str(path), str(out), '', {}, 'k', 'm', 'u',
                      source_lang='ru')

    raw = out.read_text(encoding='utf-8')
    text = [c['text'] for c in read_subtitles(str(out), mark_garbled=False)]
    assert text[0] == '第一行\\N第二行'
    # 每条 Dialogue 必须仍是单行，否则 ASS 结构被撑断
    for line in raw.splitlines():
        assert line.count('Dialogue:') <= 1


# ═══════════════════════════════════════════════════════════════
# 跨集变体归一：同一句歌词的细微写法差异只翻一次
# ═══════════════════════════════════════════════════════════════

def test_variant_lyrics_share_one_canonical_translation(tmp_path, fake_translate):
    def episode(name, lyric):
        return _write_ass(tmp_path / name, [
            (100, 102, 'Привет', 'Default'),
            (0, 3.5, lyric, 'Opening Rus'),
            (1460, 1463, 'Волшебное мамбо', 'Opening Rus'),
            (1490, 1500, 'Конец', 'Default'),
        ])

    variant = OP_LINES[0].replace('!', ',!')      # 只差一个标点
    ep1 = episode('EP001.ass', OP_LINES[0])
    ep2 = episode('EP002.ass', variant)

    oped = _collect(tmp_path)
    assert len(oped['op_map']) == 1                # 只翻规范句
    assert variant in oped['op_alias']             # 变体走别名

    texts = []
    for path, name in ((ep1, 'EP001.ass'), (ep2, 'EP002.ass')):
        cues = read_subtitles(path, mark_garbled=False)
        tr.apply_oped_pre_replace(cues, name, oped)
        texts.append([c['text'] for c in cues
                      if c['text'].startswith('译文')])
    assert texts[0] == texts[1]                    # 两集歌词措辞一致
