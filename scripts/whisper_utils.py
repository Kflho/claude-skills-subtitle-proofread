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
# 3. SRT 解析
# ═══════════════════════════════════════════════════════════════

def parse_srt(path, mark_garbled=True):
    """解析 SRT 文件，返回带时间戳的 cue 列表。
    每个 cue: {start, end, start_s, end_s, text, line, is_garbled?}
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
            c['is_garbled'] = bool(
                c['text']
                and not c['text'].startswith('[')
                and re.search(r'[a-zA-Z]{2,}', c['text'])
            )
        cues.append(c)

    # OP/ED 豁免（开头95s + 结尾120s 不标记为乱码）
    if mark_garbled and cues:
        max_end_s = max(c['end_s'] for c in cues)
        for c in cues:
            if c.get('is_garbled') and (c['start_s'] < 95 or c['start_s'] > max_end_s - 120):
                c['is_garbled'] = False

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
# 4. Whisper CLI
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
# 5. ffmpeg 音频
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
# 6. 日语质量判断
# ═══════════════════════════════════════════════════════════════

def is_valid_japanese(text):
    """判断文本是否为有效日语（含假名/汉字，非纯罗马字幻觉）。"""
    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_kanji = bool(re.search(r'[一-鿿]', text))
    is_pure_romaji = bool(re.fullmatch(r'[a-zA-Z\s\d.,!?\'\"\-]+', text))
    return (has_kana or has_kanji) and not is_pure_romaji
