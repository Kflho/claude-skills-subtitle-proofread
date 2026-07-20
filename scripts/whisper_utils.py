#!/usr/bin/env python3
"""Whisper 共享工具模块 — 消除 whisper_transcribe/full_episode/deep_fix 之间的重复代码。

提供: 时间码转换、Windows UTF-8、集号提取、SRT解析/写回、
      Whisper CLI调用、ffmpeg音频提取。
"""

import sys, os, re, subprocess, json, io

# ═══════════════════════════════════════════════════════════════
# 1. 平台 & 路径
# ═══════════════════════════════════════════════════════════════

def setup_windows_utf8():
    """Windows 下设置 stdout/stderr UTF-8。"""
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def extract_ep_number(filepath):
    """从 SRT/视频文件名提取集号，如 '철완...031 EP...srt' → 'EP031'。"""
    basename = os.path.basename(filepath)
    # 格式: ... - NNN EP. ...
    m = re.search(r'\b(\d{3})\b', basename)
    if m:
        return f'EP{m.group(1)}'
    # fallback: 用连字符后第一段
    parts = basename.split('-')
    if len(parts) >= 2:
        num = parts[1].strip().split()[0]
        if num.isdigit():
            return f'EP{num}'
    return '???'


# ═══════════════════════════════════════════════════════════════
# 2. 时间码
# ═══════════════════════════════════════════════════════════════

def to_seconds(tc):
    """时间码字符串 → 浮点秒数。支持 'HH:MM:SS,mmm' 和 'HH:MM:SS.mmm'。"""
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def format_tc(seconds):
    """浮点秒数 → SRT 时间码 'HH:MM:SS,mmm'。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')


# ═══════════════════════════════════════════════════════════════
# 3. 统一乱码分类（全项目唯一检测逻辑源）
# ═══════════════════════════════════════════════════════════════

# 已知幻觉模式 — 时代错位词（AI 将噪声"听"成不可能出现的现代词汇）
HALLUCINATION_PATTERNS = [
    r'\b(i?phone|iphone)\b', r'\bgoogle\b', r'\byoutube\b', r'\btwitter\b',
    r'\bfacebook\b', r'\binstagram\b', r'\btiktok\b', r'\bnetflix\b',
    r'\bwindows\b', r'\bmicrosoft\b', r'\bamazon\b',
]

HALLUCINATION_RE = re.compile('|'.join(HALLUCINATION_PATTERNS), re.IGNORECASE)

# 有效日语内容检测（用于区分纯罗马字 vs 混合行）
KANA_RE = re.compile(r'[぀-ヿ]')
KANJI_RE = re.compile(r'[一-鿿]')
LATIN_RE = re.compile(r'[a-zA-Z]{2,}')
CYRILLIC_RE = re.compile(r'[А-Яа-яЁё]')
NOISE_RE = re.compile(r'^[a-zA-Z\s\d\-\.\,\!\?\'\"\+]{1,2}$')  # ≤2 字符的纯拉丁噪声


def classify_garbled_text(text):
    """统一乱码分类 — 全项目唯一的字符层检测逻辑源。

    替代分散在 bilingual_detect.py、source_lang_detect.py、
    source_char_detect.py、issue_tracker.py、parse_srt() 中的重复正则。

    Args:
        text: 字幕文本（已 strip 标签）

    Returns:
        dict: {
            'type': str,        # 分类标签
            'is_garbled': bool, # 是否属于乱码（供 Whisper 阶段使用）
            'is_deletable': bool, # 是否可直接删除（短噪声碎片）
            'has_kana': bool,   # 是否含假名
            'has_kanji': bool,  # 是否含汉字
        }

    type 分类:
        'clean'                 — 无外文字符，正常文本
        'pure_romaji'           — 纯拉丁字母，无假名/汉字（需词典修复或 Whisper）
        'mixed_romaji'          — 拉丁字母 + 假名/汉字混合（需上下文修复）
        'ai_noise'              — 极短拉丁碎片（≤2 字符），可直接删除
        'hallucination'         — 时代错位幻觉词（iPhone, Google 等）
        'cyrillic'              — 含西里尔字母残留
        'music_tag'             — 以 [ 开头的音乐/音效标签（非乱码）
    """
    text = text.strip()
    if not text:
        return {'type': 'clean', 'is_garbled': False, 'is_deletable': False,
                'has_kana': False, 'has_kanji': False}

    # 音乐/音效标签（[音楽], [拍手] 等）
    if text.startswith('['):
        return {'type': 'music_tag', 'is_garbled': False, 'is_deletable': False,
                'has_kana': False, 'has_kanji': False}

    has_kana = bool(KANA_RE.search(text))
    has_kanji = bool(KANJI_RE.search(text))
    has_latin = bool(LATIN_RE.search(text))
    has_cyrillic = bool(CYRILLIC_RE.search(text))

    # 西里尔残留
    if has_cyrillic:
        return {'type': 'cyrillic', 'is_garbled': True, 'is_deletable': False,
                'has_kana': has_kana, 'has_kanji': has_kanji}

    # 无拉丁字符 → 干净
    if not has_latin:
        return {'type': 'clean', 'is_garbled': False, 'is_deletable': False,
                'has_kana': has_kana, 'has_kanji': has_kanji}

    # 时代错位幻觉
    if HALLUCINATION_RE.search(text):
        return {'type': 'hallucination', 'is_garbled': True, 'is_deletable': True,
                'has_kana': has_kana, 'has_kanji': has_kanji}

    # 纯拉丁字符（无日语内容）
    if not has_kana and not has_kanji:
        # 极短噪声（≤2 字符）
        if NOISE_RE.match(text):
            return {'type': 'ai_noise', 'is_garbled': True, 'is_deletable': True,
                    'has_kana': False, 'has_kanji': False}
        # 较长的纯罗马字
        return {'type': 'pure_romaji', 'is_garbled': True, 'is_deletable': False,
                'has_kana': False, 'has_kanji': False}

    # 拉丁 + 日语混合
    return {'type': 'mixed_romaji', 'is_garbled': True, 'is_deletable': False,
            'has_kana': has_kana, 'has_kanji': has_kanji}


# ═══════════════════════════════════════════════════════════════
# 4. SRT 解析
# ═══════════════════════════════════════════════════════════════

def parse_srt(path, mark_garbled=True):
    """解析 SRT 文件，返回带时间戳的 cue 列表。
    每个 cue: {start, end, start_s, end_s, text, line, is_garbled?, garbled_type?}
    """
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(path)
    cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        c = {
            'start': cue['start'],
            'end': cue['end'],
            'text': cue['text'].strip(),
            'line': cue.get('_start_line'),
            'start_s': to_seconds(cue['start']),
            'end_s': to_seconds(cue['end']),
        }
        if mark_garbled:
            classification = classify_garbled_text(c['text'])
            c['is_garbled'] = classification['is_garbled']
            c['garbled_type'] = classification['type']
        cues.append(c)

    # OP/ED 豁免（开头95s + 结尾120s 不标记为乱码）
    if mark_garbled and cues:
        max_end_s = max(c['end_s'] for c in cues)
        for c in cues:
            if c.get('is_garbled') and (c['start_s'] < 95 or c['start_s'] > max_end_s - 120):
                c['is_garbled'] = False
                c['garbled_type'] = 'clean'

    return cues


def write_srt(path, cues):
    """将 cue 列表写回 SRT 文件。"""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig') as f:
        for i, c in enumerate(cues, 1):
            f.write(f'{i}\n')
            f.write(f'{c["start"]} --> {c["end"]}\n')
            f.write(f'{c["text"]}\n\n')


def apply_fixes_to_srt(path, fixes):
    """将修复列表写入 SRT。fixes: [{'start': '...', 'replacement': '...'}, ...]
    返回成功修复数。
    """
    cues = parse_srt(path, mark_garbled=False)
    fixed = 0
    for cue in cues:
        for f in fixes:
            if f['start'] == cue['start'] and f.get('replacement'):
                cue['text'] = f['replacement']
                fixed += 1
                break
    write_srt(path, cues)
    return fixed


# ═══════════════════════════════════════════════════════════════
# 5. Whisper CLI
# ═══════════════════════════════════════════════════════════════

def run_whisper(audio_path, whisper_cli, model_path, language='ja',
                threads=8, processors=2, beam_size=5, best_of=8,
                nth=0.6, max_context=0, no_fallback=False):
    """调用 whisper.cpp CLI 转录音频。返回 [{start_s, end_s, text}, ...]。

    参数:
        nth: 无声阈值 (0.0-1.0)，越低越敏感。Tier 1/2 用 0.6，Tier 3 用 0.3。
        max_context: 跨段上下文 token 数，0=禁用。
        no_fallback: True=禁用 temperature fallback（不推荐，幻觉后处理已兜底）。
    """
    cmd = [
        whisper_cli, '-m', model_path, '-f', audio_path, '-l', language,
        '-t', str(threads), '-p', str(processors),
        '-bs', str(beam_size), '-bo', str(best_of),
        '-oj', '-of', audio_path + '.whisper', '--print-progress',
        '-nth', str(nth),
        '-mc', str(max_context),
        '-sns',
    ]
    if no_fallback:
        cmd.insert(-1, '-nf')  # 插入在 -sns 之前

    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stderr or '').strip().split('\n'):
        if line.strip():
            print(f'  [whisper] {line.strip()}', file=sys.stderr)

    json_path = audio_path + '.whisper.json'
    if not os.path.exists(json_path):
        print('⚠ whisper-cli 未生成 JSON', file=sys.stderr)
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print('⚠ whisper JSON 解码失败', file=sys.stderr)
        if os.path.exists(json_path):
            os.remove(json_path)
        return []
    os.remove(json_path)

    segs = []
    for seg in data.get('transcription', []):
        ts_from = seg.get('timestamps', {}).get('from', '00:00:00,000').replace(',', '.')
        ts_to = seg.get('timestamps', {}).get('to', '00:00:08,000').replace(',', '.')
        text = seg.get('text', '').strip()
        if text:
            segs.append({
                'start_s': to_seconds(ts_from),
                'end_s': to_seconds(ts_to),
                'text': text,
            })
    return segs


# ═══════════════════════════════════════════════════════════════
# 6. ffmpeg 音频
# ═══════════════════════════════════════════════════════════════

def extract_audio_wav(video_path, output_path, ss=None, duration=None):
    """从视频提取 WAV 音频（16kHz mono 无损）。可选起止时间。"""
    cmd = ['ffmpeg', '-y']
    if ss is not None:
        cmd += ['-ss', str(ss)]
    if duration is not None:
        cmd += ['-t', str(duration)]
    cmd += ['-i', video_path, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', output_path]
    subprocess.run(cmd, capture_output=True, check=True)


def get_audio_duration(audio_path):
    """用 ffprobe 获取音频时长（秒）。"""
    probe = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True)
    return float(probe.stdout.strip()) if probe.stdout.strip() else 0.0


# ═══════════════════════════════════════════════════════════════
# 7. 日语质量判断
# ═══════════════════════════════════════════════════════════════

def is_valid_japanese(text):
    """判断文本是否为有效日语（含假名/汉字，非纯罗马字幻觉）。"""
    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_kanji = bool(re.search(r'[一-鿿]', text))
    is_pure_romaji = bool(re.fullmatch(r'[a-zA-Z\s\d.,!?\'\"\-]+', text))
    return (has_kana or has_kanji) and not is_pure_romaji
