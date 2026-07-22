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

# ── OP/ED region boundaries (seconds from start/end) ──
OP_BOUNDARY_SEC = 95    # cues before this → OP region, exempt from garbled detection
ED_BOUNDARY_SEC = 120   # cues within this many seconds of the end → ED region

# ── Whisper confidence thresholds (for AI review flagging) ──
AI_REVIEW_AVG_LOGPROB_THRESHOLD = -1.0     # avg_logprob below this → uncertain
AI_REVIEW_COMPRESSION_THRESHOLD = 2.0       # compression_ratio above this → hallucination risk
AI_REVIEW_NO_SPEECH_THRESHOLD = 0.4         # no_speech_prob above this → might not be speech

# ═══════════════════════════════════════════════════════════════
# 1. 平台 & 路径
# ═══════════════════════════════════════════════════════════════

# Track whether we've already wrapped stdout/stderr to avoid double-wrapping
# which causes the old wrapper's GC to close the underlying buffer.
_utf8_setup_done = False


def setup_windows_utf8():
    """Windows 下设置 stdout/stderr UTF-8（幂等 — 多次调用安全）。"""
    global _utf8_setup_done
    if _utf8_setup_done:
        return
    _utf8_setup_done = True
    if sys.platform == 'win32':
        # Only wrap if not already a TextIOWrapper (avoid double-wrapping)
        if not isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        if not isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ═══════════════════════════════════════════════════════════════
# 集号提取 — 多策略优先级匹配（通用，不绑定特定命名格式）
# ═══════════════════════════════════════════════════════════════

# 预处理：文件名中的干扰标签（分辨率/编码/来源/音轨/位深）
_NOISE_TAGS = re.compile(
    r'\b(?:360p?|480p?|540p?|720p?|1080p?|1440p?|2160p?|4320p?|4K|8K|'
    r'x264|x265|H\.?264|H\.?265|HEVC|AVC|AV1|'
    r'BluRay|Blu-ray|WEB-DL|WEBRip|BDRip|DVDRip|HDTVRip|HDTV|'
    r'DVD|NF|AMZN|'
    r'AAC|FLAC|DTS|AC3|MP3|Opus|'
    r'8bit|10bit|Hi10p|Hi444PP)\b',
    re.IGNORECASE
)

# 分辨率数字（独立出现时排除）
_RESOLUTION_NUMS = frozenset({360, 480, 540, 720, 1080, 1440, 2160, 4320})

# 集号范围：1-4位数字，上限9999（覆盖超长番如 One Piece/柯南）
_EP_MIN = 1
_EP_MAX = 9999

# 分辨率上下文模式：数字出现在 "WxH" 或 "W×H" 附近
_RESOLUTION_CTX = re.compile(r'\b(\d{3,4})\s*[x×]\s*\d{3,4}\b')


def _is_valid_ep_number(num):
    """排除分辨率、年份、CRC32 等假阳性集号。

    排除：
    - 常见分辨率：360/480/540/720/1080/1440/2160/4320
    - 年份范围：1900-2099

    Returns: bool
    """
    if num in _RESOLUTION_NUMS:
        return False
    if 1900 <= num <= 2099:
        return False
    return _EP_MIN <= num <= _EP_MAX


def _clean_filename(basename):
    """预处理文件名：剥离扩展名、干扰标签、方括号组名。"""
    # 去扩展名
    name = os.path.splitext(basename)[0]
    # 去干扰标签（分辨率等）
    name = _NOISE_TAGS.sub(' ', name)
    # 去 CRC32 哈希 [XXXXXXXX]（8位十六进制）
    name = re.sub(r'\[[0-9A-Fa-f]{8}\]', '', name)
    # 去方括号组名 [GroupName]
    name = re.sub(r'\[[^\]]+\]', ' ', name)
    # 规范化分隔符
    name = name.replace('_', ' ').replace('.', ' ')
    # 合并多余空格
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def extract_ep_number(filepath):
    """从视频/字幕文件名提取集号。

    多策略优先级匹配，语言无关，不绑定特定命名格式。

    支持的格式（按优先级）：
    1. S01E01 / S1.E01  → 取 episode 部分
    2. 数字 + EP/ep     → "031 EP.", "108EP.", "193 END EP."
    2b. #数字           → "#193", "# 001"
    3. EP/ep + 数字     → "EP031", "Episode 5"
    4. 第X話/话/集/回   → 日/中编号
    5. 分隔符包围的数字  → " - 031 .", "_01_"
    6. 首个合理数字      → 兜底扫描

    自动排除：分辨率(360/480/540/720/1080/...)、年份(1900-2099)、CRC32。
    支持 1-9999 集号范围。3 位补零（4 位集号保持 4 位）。

    Returns: "EPxxx" (e.g. "EP001", "EP108", "EP1234") 或 "???"
    """
    basename = os.path.basename(filepath)

    # ── 策略1: S01E01 / S1.E01 格式 ──
    m = re.search(r'[Ss](\d{1,3})[\s.]*[Ee](\d{1,4})', basename)
    if m:
        ep = int(m.group(2))
        if _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略2: 数字 + EP/ep 标记 ("031 EP.", "108EP.", "001v2 EP.", "193 END EP.") ──
    # (?<!\d) 防止匹配更大数字的后缀 (如 "21080EP" 不应取 "1080")
    # \s* 允许 version 后缀（如 v2）直接附在数字后面
    m = re.search(r'(?<!\d)(\d{1,4})(?:\s*\w+)?\s*[Ee][Pp]\b', basename)
    if m:
        ep = int(m.group(1))
        if _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略2b: #数字 格式 ("#193", "# 193") ──
    m = re.search(r'#\s*(\d{1,4})\b', basename)
    if m:
        ep = int(m.group(1))
        if _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略3: EP/ep + 数字 ("EP031", "Episode 5", "Episode.5") ──
    m = re.search(r'[Ee][Pp](?:isode)?\s*\.?\s*(\d{1,4})\b', basename)
    if m:
        ep = int(m.group(1))
        if _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略4: 日/中编号 ("第01話", "第5集", "第3回") ──
    m = re.search(r'第\s*(\d{1,4})\s*[話话集回]', basename)
    if m:
        ep = int(m.group(1))
        if _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略5-6: 清理后扫描数字 ──
    clean = _clean_filename(basename)

    # 排除分辨率上下文中的数字（如 "1920x1080" 中的数字）
    res_nums = set()
    for rm in _RESOLUTION_CTX.finditer(clean):
        res_nums.add(int(rm.group(1)))
    for rm in _RESOLUTION_CTX.finditer(basename):
        res_nums.add(int(rm.group(1)))

    # ── 策略5: 分隔符包围的数字（空格/连字符/#号/方括号） ──
    for m in re.finditer(r'(?:^|[\s\-\[\]#])(\d{1,4})(?=[\s\-\[\]#]|$)', clean):
        ep = int(m.group(1))
        if ep not in res_nums and _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

    # ── 策略6: 任意位置的第一个有效数字（兜底） ──
    for m in re.finditer(r'\b(\d{1,4})\b', clean):
        ep = int(m.group(1))
        if ep not in res_nums and _is_valid_ep_number(ep):
            return f'EP{ep:03d}'

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


def classify_garbled_text(text, target_lang='ja'):
    """统一乱码分类 — v4.0 简化为 2 类。语言感知。

    不再细分纯罗马字/混合行/噪声/幻觉/西里尔。词典修复已砍，
    所有含非目标语言字符的 cue 一律送 VAD + Whisper 用音频判断。

    Args:
        text: 字幕文本（已 strip 标签）
        target_lang: 目标语言 'ja'=日语, 'zh'=中文。决定哪些字符算"外文"。

    Returns:
        dict: {
            'type': 'clean' | 'garbled',
            'has_kana': bool,
            'has_kanji': bool,
        }
    """
    text = text.strip()
    if not text:
        return {'type': 'clean', 'has_kana': False, 'has_kanji': False}

    has_kana = bool(KANA_RE.search(text))
    has_kanji = bool(KANJI_RE.search(text))
    has_latin = bool(LATIN_RE.search(text))
    has_cyrillic = bool(CYRILLIC_RE.search(text))

    if target_lang == 'zh':
        # 中文项目：假名也是外文字符。仅汉字（无假名/拉丁/西里尔）为干净。
        if not has_latin and not has_cyrillic and not has_kana:
            return {'type': 'clean', 'has_kana': False, 'has_kanji': has_kanji}
    else:
        # 日语项目：假名+汉字均为目标字符。无拉丁/西里尔为干净。
        if not has_latin and not has_cyrillic:
            return {'type': 'clean', 'has_kana': has_kana, 'has_kanji': has_kanji}

    # 有任何外文字符 → 乱码，送 VAD + Whisper
    return {'type': 'garbled', 'has_kana': has_kana, 'has_kanji': has_kanji}


# ═══════════════════════════════════════════════════════════════
# 4. SRT 解析
# ═══════════════════════════════════════════════════════════════

def parse_srt(path, mark_garbled=True, op_boundary=OP_BOUNDARY_SEC, ed_boundary=ED_BOUNDARY_SEC, target_lang='ja'):
    """解析 SRT 文件，返回带时间戳的 cue 列表。
    每个 cue: {start, end, start_s, end_s, text, line, is_garbled?, garbled_type?}

    Args:
        op_boundary: OP 豁免边界（开头 N 秒不标记乱码），默认 95s
        ed_boundary: ED 豁免边界（结尾 N 秒不标记乱码），默认 120s
        target_lang: 目标语言 'ja'|'zh'，影响乱码判断
    """
    from lib.srt_utils import read_srt_file, parse_srt_cue

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
            classification = classify_garbled_text(c['text'], target_lang=target_lang)
            c['is_garbled'] = (classification['type'] == 'garbled')
            c['garbled_type'] = classification['type']
        cues.append(c)

    # OP/ED 豁免
    if mark_garbled and cues:
        max_end_s = max(c['end_s'] for c in cues)
        ed_start = max(0, max_end_s - ed_boundary)
        for c in cues:
            if c.get('is_garbled') and (
                c['start_s'] < op_boundary or c['start_s'] > ed_start
            ):
                c['is_garbled'] = False
                c['garbled_type'] = 'clean'

    return cues


def write_srt(path, cues):
    """将 cue 列表写回 SRT 文件。"""
    def _norm_ts(ts):
        """Normalize timestamp: last dot before space/end → comma (SRT spec)."""
        ts = ts.strip()
        # Replace the last '.' in the timestamp part with ',' (SRT standard)
        # Pattern: HH:MM:SS.mmm → HH:MM:SS,mmm
        if '.' in ts:
            # Find the last '.' in the timestamp (before any non-digit tail)
            parts = ts.split('.')
            if len(parts) == 2:
                # Only one dot: HH:MM:SS.mmm
                ts = f'{parts[0]},{parts[1]}'
        return ts

    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig') as f:
        for i, c in enumerate(cues, 1):
            f.write(f'{i}\n')
            f.write(f'{_norm_ts(c["start"])} --> {_norm_ts(c["end"])}\n')
            f.write(f'{c["text"]}\n\n')


def apply_fixes_to_srt(path, fixes):
    """将修复列表写入 SRT。fixes: [{'start': '...', 'replacement': '...'}, ...]
    返回成功修复数。已正确的 cue 计入 fixed 但不重写。
    """
    cues = parse_srt(path, mark_garbled=False)
    fixed = 0
    changed = False
    for cue in cues:
        for f in fixes:
            if f['start'] == cue['start'] and f.get('replacement'):
                if cue['text'] == f['replacement']:
                    fixed += 1  # Already correct — count but don't rewrite
                    break
                cue['text'] = f['replacement']
                fixed += 1
                changed = True
                break
    if changed:
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

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=1800)
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
            'avg_logprob': seg.get('avg_logprob', None),
            'compression_ratio': seg.get('compression_ratio', None),
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
        alp = seg.get('avg_logprob', None)
        cr = seg.get('compression_ratio', None)

        if nsp >= 0 and nsp > no_speech_threshold:
            discarded.append({**seg, 'discard_reason': f'no_speech_prob={nsp:.2f}'})
        elif alp is not None and alp < min_avg_logprob:
            discarded.append({**seg, 'discard_reason': f'avg_logprob={alp:.2f}'})
        elif cr is not None and cr > max_compression_ratio:
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
        capture_output=True, text=True, encoding='utf-8', errors='replace')

    dur = get_audio_duration(input_audio)
    if dur is None or dur <= 0:
        shutil.copy2(input_audio, output_audio)
        return [(0, 1.0)], 1.0, 1.0

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
            f.write(f"file '{seg_path}'\n")

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
    # Windows: 确保 ffmpeg 子进程用 UTF-8 编码处理文件路径
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    subprocess.run(cmd, capture_output=True, check=True, encoding='utf-8', errors='replace', env=env)


def get_audio_duration(audio_path):
    """用 ffprobe 获取音频时长（秒）。"""
    probe = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', audio_path],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return float(probe.stdout.strip()) if probe.stdout.strip() else None


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
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=600)
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


def looks_like_plausible_japanese(text, target_lang='ja'):
    """可读性优先判断 — 文本是否像一句可读的目标语言。

    比 classify_garbled_text 更宽松：只要看起来像正常台词就通过。
    不要求完美正确 — Whisper 听错一两个音但仍可读的句子也放行。

    日语条件：含假名或汉字 + 不含拉丁字母 + 长度≥3 + 非纯数字/符号
    中文条件：含汉字 + 不含假名 + 含拉丁也算可读（如英文名）

    Returns True if the text looks like readable target-language content.
    """
    text = text.strip()
    if not text or len(text) < 3:
        return False

    has_kana = bool(re.search(r'[぀-ヿ]', text))
    has_kanji = bool(re.search(r'[一-鿿]', text))
    has_latin = bool(re.search(r'[a-zA-Z]', text))
    has_cyrillic = bool(re.search(r'[А-яЁё]', text))
    has_hanzi = bool(re.search(r'[一-鿿㐀-䶿]', text))

    if target_lang == 'zh':
        # 中文：有汉字即通过（允许拉丁共存，如英文名）
        if has_hanzi and not has_cyrillic:
            return True
        # 纯拉丁但在中文语境中也可能是正常的（如"OK"）
        if has_latin and not has_cyrillic and len(text) <= 10:
            return True
        return False
    else:
        # 日语：有假名或汉字 + 无拉丁 + 无西里尔
        return (has_kana or has_kanji) and not has_latin and not has_cyrillic


def is_short_garbled_fragment(text, target_lang='ja'):
    """[DEPRECATED — triage now uses meaningful_jp_count + looks_like_plausible_japanese]
    判断是否为短碎片（AI 可根据上下文补全）。"""
    text = text.strip()
    if not text:
        return False
    if target_lang == 'ja':
        latin_chars = re.findall(r'[a-zA-Z]', text)
        has_jp = bool(re.search(r'[぀-ヿ一-鿿]', text))
        # Must have some Japanese content to be context-completable;
        # pure Latin noise (me, re, go) → L6 auto-cut, not L2.5
        return has_jp and len(latin_chars) <= 5 and len(text) <= 15
    else:
        return len(text) <= 8 and not re.search(r'[一-鿿]', text)


def is_ai_fixable(text, target_lang='ja'):
    """[DEPRECATED — triage now uses meaningful_jp_count + looks_like_plausible_japanese]
    判断文本是否可由 AI 根据上下文推断修复。"""
    text = text.strip()
    if not text:
        return False

    if target_lang == 'ja':
        has_kana = bool(re.search(r'[぀-ヿ]', text))
        has_kanji = bool(re.search(r'[一-鿿]', text))
        has_latin = bool(re.search(r'[a-zA-Z]', text))

        # 要有日语语义 且 有拉丁乱码
        if not (has_kana or has_kanji):
            return False
        if not has_latin:
            return False
        # 不要太长（一句话以内 AI 才能准确推断）
        if len(text) > 80:
            return False
        return True
    else:
        # 中文：有汉字 + 拉丁乱码 + 不太长
        has_hanzi = bool(re.search(r'[一-鿿]', text))
        has_latin = bool(re.search(r'[a-zA-Z]', text))
        return has_hanzi and has_latin and len(text) <= 80


def is_proper_noun_pattern(text):
    """判断文本是否符合专名模式（片假名/汉字名 → 应送 L3 而非 AI 补全）。"""
    # 纯片假名 → 可能是人名/角色名
    if re.fullmatch(r'[゠-ヿー]{2,}', text):
        return True
    # 汉字组合 → 可能是日本人名
    if re.fullmatch(r'[一-鿿]{2,4}', text):
        return True
    # 片假名+拉丁混合 → 可能是外来语专名
    if re.search(r'[゠-ヿ]', text) and re.search(r'[a-zA-Z]', text):
        return True
    return False


# ── Kana exclusively used in exclamations/grunts/sound-effects ──
# あっ！えーっ！おっ！うんうん… are non-verbal sounds, not dialogue.
# Used by meaningful_jp_count() to distinguish real speech from noise.
EXCLAMATION_KANA = frozenset(
    'あいうえおぁぃぅぇぉっーん〜'
    'アイウエオァィゥェォッ'
)


def meaningful_jp_count(text):
    """Count Japanese characters that are NOT just exclamations/grunts.

    Pure kana like あっ！えーっ！うんうん… are non-verbal sounds
    that Whisper transcribed as kana; they don't indicate real dialogue.
    Only kana/kanji outside EXCLAMATION_KANA count as evidence of speech.

    Returns:
        int: number of meaningful Japanese characters
    """
    text = text.strip()
    if not text:
        return 0
    all_jp = sum(1 for c in text if 'ぁ' <= c <= 'ヿ' or '一' <= c <= '鿿')
    exclamation_jp = sum(1 for c in text if c in EXCLAMATION_KANA)
    return all_jp - exclamation_jp


def is_length_anomaly(original, whisper_text, ratio=3.0):
    """Detect suspicious Whisper output significantly longer than original.

    Hallucinations often produce verbose but semantically wrong output
    from short garbled input.  A ratio > 3.0 strongly suggests hallucination
    rather than genuine correction.

    Args:
        original: original garbled text
        whisper_text: Whisper's attempted correction
        ratio: length ratio threshold (default 3.0)

    Returns:
        True if whisper_text is suspiciously long compared to original
    """
    orig_len = max(len(original.strip()), 3)
    whisper_len = len(whisper_text.strip())
    if whisper_len == 0:
        return False
    return whisper_len / orig_len > ratio
