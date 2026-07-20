#!/usr/bin/env python3
"""Whisper 共享工具模块 — 消除 whisper_transcribe/full_episode/deep_fix 之间的重复代码。

提供: 时间码转换、Windows UTF-8、集号提取、SRT解析/写回、
      Whisper CLI调用、ffmpeg音频提取、VAD预过滤、置信度评估。

v2.1 变更:
  - 移除默认 -sns（已知在音乐/静音段制造幻觉）；改为 opt-in suppress_nst
  - 新增 VAD 预过滤 vad_filter_audio()（ffmpeg silencedetect）
  - 新增置信度指标解析（no_speech_prob, avg_logprob, compression_ratio）
  - 新增 filter_low_confidence() 三道防线过滤
  - parse_srt() OP/ED 边界可配置
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

def parse_srt(path, mark_garbled=True, op_boundary=95, ed_boundary=120):
    """解析 SRT 文件，返回带时间戳的 cue 列表。
    每个 cue: {start, end, start_s, end_s, text, line, is_garbled?, garbled_type?}

    Args:
        op_boundary: OP 豁免边界（开头 N 秒不标记乱码），默认 95s
        ed_boundary: ED 豁免边界（结尾 N 秒不标记乱码），默认 120s
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

    # OP/ED 豁免
    if mark_garbled and cues:
        max_end_s = max(c['end_s'] for c in cues)
        for c in cues:
            if c.get('is_garbled') and (
                c['start_s'] < op_boundary or c['start_s'] > max_end_s - ed_boundary
            ):
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
                nth=0.6, max_context=0, no_fallback=False,
                suppress_nst=False):
    """调用 whisper.cpp CLI 转录音频。

    返回 [{start_s, end_s, text, no_speech_prob, avg_logprob, compression_ratio}, ...]

    参数:
        nth: 无声阈值 (0.0-1.0)，越低越敏感。Tier 1/2 用 0.6，Tier 3 用 0.3。
        max_context: 跨段上下文 token 数，0=禁用。
        no_fallback: True=禁用 temperature fallback（不推荐，幻觉后处理已兜底）。
        suppress_nst: 启用 -sns（非语音 token 抑制）。
                      ⚠️ 默认 False。研究证实 -sns 会在非语音段制造幻觉；
                      仅当外部 VAD 完全移除音乐/静音后才考虑启用。
    """
    # 不默认启用 -sns：研究证实 suppress_non_speech_tokens 在音乐/静音段
    # 导致模型编造文字 (Calm-Whisper IS2025; whisper.cpp #1258, #2137)
    cmd = [
        whisper_cli, '-m', model_path, '-f', audio_path, '-l', language,
        '-t', str(threads), '-p', str(processors),
        '-bs', str(beam_size), '-bo', str(best_of),
        '-oj', '-of', audio_path + '.whisper', '--print-progress',
        '-nth', str(nth),
        '-mc', str(max_context),
    ]
    if suppress_nst:
        cmd.append('-sns')
    if no_fallback:
        cmd.append('-nf')

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
        if not text:
            continue
        segs.append({
            'start_s': to_seconds(ts_from),
            'end_s': to_seconds(ts_to),
            'text': text,
            # 置信度指标（whisper.cpp JSON 输出字段）
            'no_speech_prob': seg.get('no_speech_prob', -1.0),
            'avg_logprob': seg.get('avg_logprob', 0.0),
            'compression_ratio': seg.get('compression_ratio', 0.0),
        })
    return segs


def filter_low_confidence(segs, no_speech_threshold=0.6, min_avg_logprob=-1.5,
                          max_compression_ratio=2.4):
    """过滤低置信度/疑似幻觉的 Whisper 输出段（whisper.cpp 社区推荐三道防线）。

    Returns:
        (kept, discarded): 两个列表
    """
    kept, discarded = [], []
    for seg in segs:
        nsp = seg.get('no_speech_prob', -1.0)
        alp = seg.get('avg_logprob', 0.0)
        cr = seg.get('compression_ratio', 0.0)

        if nsp >= 0 and nsp > no_speech_threshold:
            discarded.append({**seg, 'discard_reason': f'no_speech_prob={nsp:.2f}'})
        elif alp < min_avg_logprob:
            discarded.append({**seg, 'discard_reason': f'avg_logprob={alp:.2f}'})
        elif cr > max_compression_ratio:
            discarded.append({**seg, 'discard_reason': f'compression_ratio={cr:.1f}'})
        else:
            kept.append(seg)
    return kept, discarded


# ═══════════════════════════════════════════════════════════════
# 6. ffmpeg 音频
# ═══════════════════════════════════════════════════════════════

def vad_filter_audio(input_audio, output_audio, silence_db=-30, min_silence=0.8,
                     min_speech=0.3, padding=0.15):
    """用 ffmpeg silencedetect 预过滤非语音段，减少 Whisper 幻觉触发源。

    原理：Whisper 在静音/音乐段产生幻觉（研究显示 99.97% 的非语音音频触发）。
    VAD 预过滤从源头去除非语音段，比后处理清理更有效。

    Args:
        input_audio: 输入 WAV 路径
        output_audio: 输出（仅含语音段拼接的）WAV 路径
        silence_db: 静音判定 dB 阈值（默认 -30）
        min_silence: 判定静音的最短时长（秒，默认 0.8）
        min_speech: 保留语音段的最短时长（秒，默认 0.3）
        padding: 语音段前后保留的缓冲（秒，默认 0.15）

    Returns:
        (speech_segments, total_duration, speech_duration):
          speech_segments: [(start_s, end_s), ...] 检测到的语音段时间对
          total_duration: 原始音频时长
          speech_duration: 语音段总时长
    """
    import shutil

    # 1. 用 ffmpeg silencedetect 找静音区间
    proc = subprocess.run(
        ['ffmpeg', '-i', input_audio,
         '-af', f'silencedetect=n={silence_db}dB:d={min_silence}',
         '-f', 'null', '-'],
        capture_output=True, text=True)

    dur = get_audio_duration(input_audio)

    silence_starts = []
    silence_ends = []
    for line in (proc.stderr or '').splitlines():
        m_start = re.search(r'silence_start:\s*([\d.]+)', line)
        m_end = re.search(r'silence_end:\s*([\d.]+)', line)
        if m_start:
            silence_starts.append(float(m_start.group(1)))
        elif m_end:
            silence_ends.append(float(m_end.group(1)))

    # 2. 从静音区间反推语音区间
    if not silence_starts:
        shutil.copy2(input_audio, output_audio)
        return [(0, dur)], dur, dur

    # 对齐 start/end：ffmpeg 可能产生不成对的事件（音频以语音开始→无 silence_start，
    # 以静音结束→无 silence_end）。补齐使 zip 不会截断。
    # 如果第一个事件是 silence_end（音频以语音开始，静音结束），
    # 则插入 silence_start=0.0。
    if silence_ends and (not silence_starts or silence_ends[0] < silence_starts[0]):
        silence_starts.insert(0, 0.0)
    # 截断到较短列表的长度
    n = min(len(silence_starts), len(silence_ends))
    silence_starts = silence_starts[:n]
    silence_ends = silence_ends[:n]

    speech_segs = []
    prev_end = 0.0
    for sil_start, sil_end in zip(silence_starts, silence_ends):
        if sil_start - prev_end >= min_speech:
            ss = max(0, prev_end - padding)
            es = min(dur, sil_start + padding)
            if es - ss >= min_speech:
                speech_segs.append((ss, es))
        prev_end = sil_end
    # 最后一段
    if dur - prev_end >= min_speech:
        ss = max(0, prev_end - padding)
        es = dur
        if es - ss >= min_speech:
            speech_segs.append((ss, es))

    if not speech_segs:
        shutil.copy2(input_audio, output_audio)
        return [], dur, 0

    # 3. 拼接语音段
    os.makedirs(os.path.dirname(output_audio) if os.path.dirname(output_audio) else '.', exist_ok=True)
    concat_file = output_audio + '.concat.txt'
    with open(concat_file, 'w') as f:
        for i, (ss, es) in enumerate(speech_segs):
            seg_path = output_audio + f'.seg{i:03d}.wav'
            subprocess.run([
                'ffmpeg', '-y', '-ss', str(ss), '-t', str(es - ss),
                '-i', input_audio, '-c', 'copy', seg_path
            ], capture_output=True, check=True)
            f.write(f"file '{seg_path}'
")

    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_file, '-c', 'copy', output_audio
    ], capture_output=True, check=True)

    # 清理临时文件
    for i in range(len(speech_segs)):
        seg_path = output_audio + f'.seg{i:03d}.wav'
        if os.path.exists(seg_path):
            os.remove(seg_path)
    if os.path.exists(concat_file):
        os.remove(concat_file)

    speech_dur = sum(es - ss for ss, es in speech_segs)
    return speech_segs, dur, speech_dur


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
# 7. 人声分离（demucs）
# ═══════════════════════════════════════════════════════════════

def separate_vocals(audio_path, output_dir=None, python_exe=None):
    """用 demucs 分离人声（去掉 BGM/音效，减少 Whisper 幻觉触发源）。

    注意：demucs 是音乐源分离，不是传统降噪。研究证实传统降噪会破坏
    Whisper 需要的声学特征（WER +3~35%），但移除背景音乐对动漫 ASR 有益
    因为 BGM 是 Whisper 幻觉的主要触发源。

    Args:
        audio_path: 输入 WAV 路径
        output_dir: 输出目录（默认同输入目录）
        python_exe: Python 解释器路径（默认 sys.executable）

    Returns:
        vocals_path: 人声文件路径，失败时返回原始 audio_path
    """
    if python_exe is None:
        python_exe = sys.executable

    out_dir = output_dir or os.path.dirname(audio_path)
    os.makedirs(out_dir, exist_ok=True)

    try:
        proc = subprocess.run(
            [python_exe, '-m', 'demucs', '--two-stems=vocals',
             '-o', out_dir, audio_path],
            capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print('⚠ demucs 不可用或超时，跳过人声分离', file=sys.stderr)
        return audio_path

    if proc.returncode != 0:
        print(f'⚠ demucs 失败，使用原始音频: {proc.stderr[-200:]}', file=sys.stderr)
        return audio_path

    # demucs 输出路径: <out_dir>/htdemucs/<basename>/vocals.wav
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    vocals = os.path.join(out_dir, 'htdemucs', basename, 'vocals.wav')
    if os.path.exists(vocals):
        print(f'  [demucs] 人声分离完成 → {vocals}', file=sys.stderr)
        return vocals

    print('⚠ demucs 输出文件未找到，使用原始音频', file=sys.stderr)
    return audio_path


# ═══════════════════════════════════════════════════════════════
# 8. 日语质量判断
# ═══════════════════════════════════════════════════════════════

def is_valid_japanese(text):
    """判断文本是否为有效日语（含假名/汉字，非纯罗马字幻觉）。"""
    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_kanji = bool(re.search(r'[一-鿿]', text))
    is_pure_romaji = bool(re.fullmatch(r'[a-zA-Z\s\d.,!?\'\"\-]+', text))
    return (has_kana or has_kanji) and not is_pure_romaji
