#!/usr/bin/env python3
"""统一字符扫描器 — 单次遍历检测所有源语言字符残留 + VAD 无字幕检测。

替代 4 个重复扫描脚本：
  - bilingual_detect.py   (双语混合 + 纯源语言)
  - source_lang_detect.py (纯源语言行)
  - source_char_detect.py (多语言字符残留)
  - issue_tracker.py      (Whisper 问题清单)

核心设计：
  1. 每个文件只读取一次
  2. 每个 cue 调用 classify_garbled_text() 统一分类
  3. 一次输出三样东西：findings JSON + per-episode issues + delete candidates
  4. (v5.0) VAD 有人声无字幕检测 — 作为第一类错误与 garbled 并列

用法:
  python unified_scanner.py --target-dir <DIR> --output-findings findings.json

  同时生成 issues 和 delete candidates:
  python unified_scanner.py --target-dir <DIR> \\
    --output-findings findings.json \\
    --output-issues issues/ \\
    --output-delete delete_candidates.json

  项目感知（跳过不适用的分类）:
  python unified_scanner.py --target-dir <DIR> --project-lang ja --format srt

  VAD 无字幕检测（需视频文件）:
  python unified_scanner.py --target-dir <DIR> --video-dir <VIDEOS> \\
    --output-findings findings.json
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

import lib._path  # noqa: F401

from lib.ass_utils import (
    strip_ass_tags, iter_ass_files, iter_dialogue_lines,
    read_ass_file, contains_cjk,
)
from lib.whisper_utils import (
    classify_garbled_text, to_seconds, extract_ep_number, extract_file_id,
    setup_windows_utf8, OP_BOUNDARY_SEC, ED_BOUNDARY_SEC,
    parse_srt, parse_subtitles, extract_audio_wav,
)


# ── jieba initialization (lazy, once) ──
_jieba_initialized = False
_jieba_available = False


def _ensure_jieba():
    """Lazy-init jieba for Chinese word segmentation."""
    global _jieba_initialized, _jieba_available
    if _jieba_initialized:
        return _jieba_available
    _jieba_initialized = True
    try:
        import jieba
        jieba.initialize()
        _jieba_available = True
    except (ImportError, Exception) as e:
        print(f'[unified_scanner] jieba unavailable ({e}) — '
              f'falling back to n-gram extraction', file=__import__('sys').stderr)
        _jieba_available = False
    return _jieba_available


def _segment_text_zh(text, term_freq):
    """Segment Chinese text with jieba, count word frequencies.

    Falls back to n-gram sliding window if jieba is unavailable.
    """
    if _ensure_jieba():
        import jieba
        words = jieba.lcut(text)
        for w in words:
            w = w.strip()
            # Keep only words with ≥2 CJK chars (filter punctuation, single chars, Latin)
            if len(w) >= 2 and __import__('re').search(r'[一-鿿]', w):
                term_freq[w] += 1
    else:
        # Fallback: 2-4 char n-gram sliding window
        for m in __import__('re').finditer(r'[一-鿿]{2,4}', text):
            term_freq[m.group()] += 1


# ── Janome initialization (lazy, once) ──
_janome_initialized = False
_janome_available = False
_tokenizer = None  # module-level singleton — Tokenizer() is expensive!
# Quick check: does text contain any CJK characters worth tokenizing?
_HAS_CJK_RE = None


def _ensure_janome():
    """Lazy-init Janome for Japanese morphological analysis.

    Creates Tokenizer ONCE — creating a new Tokenizer() per cue
    re-loads the system dictionary from disk each time, which is
    catastrophically slow for 193 episodes × ~200 cues.
    """
    global _janome_initialized, _janome_available, _tokenizer
    if _janome_initialized:
        return _janome_available
    _janome_initialized = True
    try:
        from janome.tokenizer import Tokenizer
        _tokenizer = Tokenizer()
        # no-op test tokenization to verify the dictionary loaded
        _tokenizer.tokenize('テスト')
        _janome_available = True
    except (ImportError, Exception) as e:
        print(f'[unified_scanner] Janome unavailable ({e}) — '
              f'falling back to n-gram extraction', file=__import__('sys').stderr)
        _janome_available = False
    return _janome_available


def _segment_text_ja(text, term_freq):
    """Segment Japanese text with Janome, count word frequencies.

    Uses POS-filtered morphological analysis — keeps only nouns
    (固有名詞 and 一般) with katakana or kanji, ≥2 characters.
    Falls back to n-gram sliding window if Janome is unavailable.

    Perf: Tokenizer is a module-level singleton (created once).
    Text without any CJK chars short-circuits before tokenization.
    """
    global _HAS_CJK_RE
    if _HAS_CJK_RE is None:
        _HAS_CJK_RE = __import__('re').compile(r'[一-鿿゠-ヿ]')
    if not _HAS_CJK_RE.search(text):
        return  # No CJK → nothing to extract for glossary

    if _ensure_janome():
        global _tokenizer
        import re as _re
        for token in _tokenizer.tokenize(text):
            surface = token.surface
            if len(surface) < 2:
                continue
            # Keep only katakana or kanji-bearing words (skip hiragana-only)
            is_kata = bool(_re.search(r'[゠-ヿ]', surface))
            is_kanji = bool(_re.search(r'[一-鿿]', surface))
            if not (is_kata or is_kanji):
                continue
            # POS filter: nouns only, exclude pronouns/suffixes/numerals
            parts = token.part_of_speech.split(',')
            pos = parts[0]
            pos2 = parts[1] if len(parts) > 1 else ''
            if pos == '名詞' and pos2 not in ('非自立', '代名词', '数', '接尾'):
                term_freq[surface] += 1
    else:
        # Fallback: n-gram (current behavior)
        for m in __import__('re').finditer(r'[゠-ヿ]{2,6}', text):
            term_freq[m.group()] += 1
        for m in __import__('re').finditer(r'[一-鿿]{2,4}', text):
            term_freq[m.group()] += 1


# ═══════════════════════════════════════════════════════════════
# 分类标签 → 人类可读描述
# ═══════════════════════════════════════════════════════════════

TYPE_LABELS = {
    'clean':   '纯目标语言，无外文字符',
    'garbled': '含外文字符 → VAD + Whisper',
}

LANG_LABELS = {
    'ja': '纯日语',
    'zh': '纯中文',
}


# ═══════════════════════════════════════════════════════════════
# VAD 有人声无字幕检测（Layer 1 第一类错误）
# ═══════════════════════════════════════════════════════════════

SUSPICIOUS_GAP_SEC = 20.0
MISSING_SUBTITLE_MIN_GAP = 3.0
MISSING_SUBTITLE_MERGE_GAP = 5.0
MISSING_SUBTITLE_MAX_GAP = 45.0


def _find_video_for_srt(srt_path, video_dir):
    """Find matching video file for an SRT by episode number."""
    if not video_dir or not os.path.isdir(video_dir):
        return None
    srt_ep = extract_ep_number(srt_path)
    if srt_ep == '???':
        srt_stem = os.path.splitext(os.path.basename(srt_path))[0]
        for fname in sorted(os.listdir(video_dir)):
            if fname.lower().endswith(('.mkv', '.mp4', '.avi', '.mov')):
                if srt_stem.lower() in fname.lower():
                    return os.path.join(video_dir, fname)
        return None
    ep_num = srt_ep[2:]
    for fname in sorted(os.listdir(video_dir)):
        if not fname.lower().endswith(('.mkv', '.mp4', '.avi', '.mov')):
            continue
        vid_ep = extract_ep_number(fname)
        if vid_ep == srt_ep:
            return os.path.join(video_dir, fname)
        if ep_num in fname:
            return os.path.join(video_dir, fname)
    return None


def detect_missing_subtitles_for_episode(srt_path, video_path, tmp_dir,
                                         min_gap=None, merge_gap=None,
                                         max_gap=None):
    """Detect speech segments without subtitle coverage (VAD-based).

    Returns:
        (gaps, speech_segs, stats):
          gaps: [(start_s, end_s, duration), ...]
          speech_segs: [(start_s, end_s), ...] full speech timeline
          stats: dict with stats
    """
    min_gap = min_gap if min_gap is not None else MISSING_SUBTITLE_MIN_GAP
    merge_gap = merge_gap if merge_gap is not None else MISSING_SUBTITLE_MERGE_GAP
    max_gap = max_gap if max_gap is not None else MISSING_SUBTITLE_MAX_GAP

    cues = parse_subtitles(srt_path, mark_garbled=False)
    if not cues:
        return [], [], {'error': 'No cues in subtitle file'}

    vad_audio = os.path.join(tmp_dir, 'vad_full.wav')
    try:
        extract_audio_wav(video_path, vad_audio)
    except Exception as e:
        print(f'[VAD scan] Audio extraction failed for '
              f'{os.path.basename(srt_path)}: {e}', file=sys.stderr)
        return [], [], {'error': str(e)}

    try:
        from fix.whisper_pipeline import get_speech_timeline, find_missing_subtitle_gaps
    except ImportError as e:
        print(f'[VAD scan] Cannot import VAD functions: {e}', file=sys.stderr)
        return [], [], {'error': str(e)}

    speech_segs = get_speech_timeline(vad_audio)
    if not speech_segs:
        return [], [], {'audio_duration_s': 0, 'speech_duration_s': 0,
                       'speech_pct': 0, 'gap_count': 0, 'total_gap_duration_s': 0}

    gaps = find_missing_subtitle_gaps(speech_segs, cues,
                                       min_gap=min_gap, merge_gap=merge_gap,
                                       max_gap=max_gap)

    speech_dur = sum(es - ss for ss, es in speech_segs)
    from lib.whisper_utils import get_audio_duration
    total_dur = get_audio_duration(vad_audio) or speech_dur
    gap_dur = sum(es - ss for ss, es, _ in gaps)

    stats = {
        'audio_duration_s': round(total_dur, 1),
        'speech_duration_s': round(speech_dur, 1),
        'speech_pct': round(100 * speech_dur / max(total_dur, 1), 1),
        'gap_count': len(gaps),
        'total_gap_duration_s': round(gap_dur, 1),
    }

    return gaps, speech_segs, stats


def _compute_gap_statistics(cues):
    """Compute inter-cue gap statistics (text-only, no audio needed)."""
    if len(cues) < 2:
        return {'max_gap_s': 0, 'suspicious_gap_count': 0, 'total_gaps': 0}
    gaps = []
    sorted_cues = sorted(cues, key=lambda c: c['start_s'])
    for i in range(1, len(sorted_cues)):
        gap = sorted_cues[i]['start_s'] - sorted_cues[i-1]['end_s']
        if gap > 0:
            gaps.append(gap)
    suspicious = [g for g in gaps if g > SUSPICIOUS_GAP_SEC]
    return {
        'max_gap_s': round(max(gaps), 1) if gaps else 0,
        'suspicious_gap_count': len(suspicious),
        'total_gaps': len(gaps),
    }


# ═══════════════════════════════════════════════════════════════
# 核心扫描逻辑
# ═══════════════════════════════════════════════════════════════

def get_oped_boundaries(cues):
    """计算 OP/ED 时间边界。开头 95s、结尾 120s 内的 cue 视为 OP/ED 区域。"""
    if not cues:
        return OP_BOUNDARY_SEC, 0
    max_end_s = max(c['end_s'] for c in cues)
    return OP_BOUNDARY_SEC, max(0, max_end_s - ED_BOUNDARY_SEC)


# ═══════════════════════════════════════════════════════════════
# 卡死重复检测（合并自 repeat_detect.py）
# ═══════════════════════════════════════════════════════════════

# 排除：scat 拟声 / 动物叫声 / 情绪表达
_EXCLUDED_SEQS = {
    'pa', 'la', 'me', 'ta',
    '汪', '喵', '哞', '咩', '咯', '咕', '嘎', '呱', '吱', '啾',
    '嗷', '呜', '哼', '嘶', '喔', '啊', '哦', '嗯', '呃',
    'woof', 'meow', 'moo', 'baa', 'quack', 'oink', 'cluck',
    'chirp', 'buzz', 'ribbit', 'neigh', 'roar', 'howl',
    'arf', 'bow', 'caw', 'coo', 'hoot', 'tweet',
    'wo', 'me', 'mu', 'ba', 'ha', 'he', 'ho', 'hi', 'hu',
}


def _is_excluded_repeat(seq, full_match):
    """综合判断是否应排除此重复序列。"""
    seq_lower = seq.lower()
    if seq_lower in _EXCLUDED_SEQS:
        return True
    # 整段仅 1-2 种字符 → 可能是情绪表达
    stripped = re.sub(r'[!！?？\s\-~～]+', '', full_match)
    if stripped:
        unique = set(stripped.lower())
        if len(unique) <= 2:
            return True
    return False


def _find_repeats(text, min_repeats=8):
    """在文本中查找 2-4 字符序列的连续重复。"""
    results = []
    for seq_len in [2, 3, 4]:
        if len(text) < seq_len * min_repeats:
            continue
        i = 0
        while i <= len(text) - seq_len * min_repeats:
            seq = text[i:i + seq_len]
            if re.search(r'[\s\-~～!！?？,，.。、；;：:]', seq):
                i += 1
                continue
            count = 1
            j = i + seq_len
            while j + seq_len <= len(text) and text[j:j + seq_len] == seq:
                count += 1
                j += seq_len
            if count >= min_repeats:
                full = text[i:j]
                if not _is_excluded_repeat(seq, full):
                    results.append({
                        'repeat_seq': seq,
                        'repeat_count': count,
                        'full_match': full,
                    })
                i = j
            else:
                i += 1
    return results


def scan_file(filepath, skip_oped=True, target_lang='ja'):
    """扫描单个字幕文件，返回该文件的分类结果。

    Args:
        filepath: SRT/ASS 文件路径
        skip_oped: 是否跳过 OP/ED 区域（开头 95s、结尾 120s）
        target_lang: 目标语言 'ja'|'zh'，影响乱码判断

    Returns:
        dict: {
            'filename': str,
            'garbled_cues': [finding, ...],
            'issues': [issue, ...],
        }
    """
    fname = os.path.basename(filepath)
    lines = read_ass_file(filepath)
    cues = []

    # 第一遍：收集所有 cue（带时间戳）
    for line_idx, d in iter_dialogue_lines(lines):
        # 跳过绘图指令行
        if '\\p1' in d.get('text', ''):
            continue
        visible = strip_ass_tags(d['text'])
        cues.append({
            'line': line_idx + 1,  # 1-based
            'start': d['start'],
            'end': d['end'],
            'text': visible,
            'raw_text': d['text'],
        })

    # 计算 OP/ED 边界
    op_boundary, ed_boundary = (95, 0)
    if cues:
        cues_with_seconds = []
        for c in cues:
            try:
                s = to_seconds(c['start'])
                e = to_seconds(c['end'])
                cues_with_seconds.append({**c, 'start_s': s, 'end_s': e})
            except (ValueError, IndexError):
                continue
        cues = cues_with_seconds
        if skip_oped:
            op_boundary, ed_boundary = get_oped_boundaries(cues)
    else:
        return {
            'filename': fname,
            'findings': {},
            'issues': [],
            'delete_candidates': [],
            'total_cues': 0,
        }

    # 第二遍：分类每个 cue + 重复检测 + 术语收集
    garbled_cues = []
    issues = []
    repeats = []
    term_freq = defaultdict(int)  # {word: count} for glossary building

    for c in cues:
        classification = classify_garbled_text(c['text'], target_lang=target_lang)
        gtype = classification['type']

        # ── 术语收集（所有 cue，按语言提取） ──
        text = c['text']
        if target_lang == 'ja':
            # Janome morphological analysis (replaces n-gram sliding window)
            # Produces actual Japanese words instead of ~40% character fragments.
            _segment_text_ja(text, term_freq)
        elif target_lang == 'zh':
            # jieba word segmentation (replaces n-gram sliding window)
            # Produces actual Chinese words instead of character fragments.
            _segment_text_zh(text, term_freq)
        elif target_lang == 'en':
            # Capitalized words: potential proper nouns in English subtitles
            for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', text):
                term_freq[m.group()] += 1
        else:
            # Fallback: CJK compounds (generic)
            for m in re.finditer(r'[一-鿿]{2,4}', text):
                term_freq[m.group()] += 1

        # ── 重复检测（所有 cue） ──
        cue_repeats = _find_repeats(c['text'])
        for r in cue_repeats:
            repeats.append({
                'file': fname,
                'line': c['line'],
                'timecode': c['start'],
                'repeat_seq': r['repeat_seq'],
                'repeat_count': r['repeat_count'],
                'full_match': r['full_match'],
            })

        if gtype == 'clean':
            continue

        # OP/ED 区域豁免
        if skip_oped and (c['start_s'] < op_boundary or c['start_s'] > ed_boundary):
            continue

        # 提取文件 ID（EP### 或 slug fallback）
        ep = extract_file_id(fname)

        # 构建 finding
        finding = {
            'file': fname,
            'line': c['line'],
            'timecode': c['start'],
            'text': c['text'][:120],
            'has_kana': classification['has_kana'],
            'has_kanji': classification['has_kanji'],
        }
        garbled_cues.append(finding)

        # 所有 garbled cue 都是 Whisper issue
        issues.append({
            'ep': ep,
            'start': c['start'],
            'end': c['end'],
            'original_text': c['text'][:120],
            'line': c['line'],
        })

    # ── Gap statistics (text-only risk indicator) ──
    gap_stats = _compute_gap_statistics(cues)

    return {
        'filename': fname,
        'garbled_cues': garbled_cues,
        'issues': issues,
        'repeats': repeats,
        'term_freq': dict(term_freq),
        'total_cues': len(cues),
        'gap_statistics': gap_stats,
    }


def _parse_episodes(arg):
    """Parse --episodes argument into a list of episode IDs.

    Supports: 'EP001-EP010', 'EP001,EP005,EP010', '1-10', '1,5,10'
    """
    if not arg:
        return None
    episodes = []
    for part in arg.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            a = int(re.sub(r'\D', '', a))
            b = int(re.sub(r'\D', '', b))
            episodes.extend(f'EP{i:03d}' for i in range(a, b + 1))
        else:
            num = int(re.sub(r'\D', '', part))
            episodes.append(f'EP{num:03d}')
    return sorted(set(episodes))


def _should_scan(ep, episodes):
    """Check if episode should be scanned based on filter list."""
    if episodes is None:
        return True
    return ep in episodes


def scan_all(target_dir, skip_oped=True, target_lang='ja',
             video_dir=None, skip_vad=False, vad_cache_dir=None,
             episodes=None):
    """扫描目录中所有字幕文件。

    Args:
        target_lang: 目标语言 'ja'|'zh'
        video_dir: 视频目录（可选）。提供后运行 VAD 有人声无字幕检测。
        skip_vad: 跳过 VAD 检测（即使有 video_dir）
        vad_cache_dir: VAD 结果缓存目录（默认 temp/scans/）
        episodes: 可选，限定扫描的集数列表 ['EP001', 'EP002', ...]
                   None 表示全部

    Returns:
        dict: {
            'garbled_cues': [finding, ...],      # 所有文件汇总
            'missing_subtitles': {ep: [gap, ...]},  # v5.0: VAD 有人声无字幕
            'per_episode_issues': {ep: [issue, ...]},
            'summary': {...},
        }
    """
    all_garbled = []
    all_issues = defaultdict(list)
    all_repeats = []
    all_terms = defaultdict(int)
    all_missing_subtitles = {}   # v5.0: {ep: [gap, ...]}
    all_gap_stats = {}           # v5.0: {ep: gap_statistics}
    total_cues = 0
    files_scanned = 0
    vad_episodes_scanned = 0
    vad_total_gaps = 0

    for fname, fpath in iter_ass_files(target_dir):
        ep = extract_file_id(fname)
        if not _should_scan(ep, episodes):
            continue
        result = scan_file(fpath, skip_oped=skip_oped, target_lang=target_lang)
        files_scanned += 1
        total_cues += result['total_cues']

        all_garbled.extend(result['garbled_cues'])
        all_repeats.extend(result.get('repeats', []))

        for word, count in result.get('term_freq', {}).items():
            all_terms[word] += count

        for issue in result['issues']:
            all_issues[issue['ep']].append(issue)

        # ── Gap statistics (text-only, always collected) ──
        all_gap_stats[ep] = result.get('gap_statistics', {})

    # ═══════════════════════════════════════════════════════════
    # v5.0: VAD 有人声无字幕检测
    # ═══════════════════════════════════════════════════════════
    if video_dir and not skip_vad and os.path.isdir(video_dir):
        print(f'\n[VAD scan] Detecting missing subtitles '
              f'(video dir: {video_dir}) ...', file=sys.stderr)

        vad_tmp = tempfile.mkdtemp(prefix='vad_scan_')
        try:
            for fname, fpath in iter_ass_files(target_dir):
                ep = extract_file_id(fname)
                if not _should_scan(ep, episodes):
                    continue
                video_path = _find_video_for_srt(fpath, video_dir)

                if not video_path:
                    continue

                print(f'  [{ep}] VAD scan ...', file=sys.stderr)

                # Check cache first
                cache_path = None
                if vad_cache_dir:
                    cache_path = os.path.join(vad_cache_dir, f'{ep}_vad.json')

                gaps, speech_segs, stats = detect_missing_subtitles_for_episode(
                    fpath, video_path, vad_tmp)

                if 'error' in stats:
                    print(f'  [{ep}] VAD failed: {stats["error"]}', file=sys.stderr)
                    continue

                vad_episodes_scanned += 1

                if gaps:
                    all_missing_subtitles[ep] = [
                        {'start_s': round(ss, 2), 'end_s': round(es, 2),
                         'duration': round(dur, 2)}
                        for ss, es, dur in gaps
                    ]
                    vad_total_gaps += len(gaps)

                    # Add missing_subtitle issues to per_episode_issues
                    for ss, es, dur in gaps:
                        all_issues[ep].append({
                            'ep': ep,
                            'start_s': round(ss, 2),
                            'end_s': round(es, 2),
                            'duration': round(dur, 2),
                            'type': 'missing_subtitle',
                            'original_text': '(VAD检测到人声但无对应字幕)',
                        })

                    print(f'  [{ep}] {len(gaps)} missing-subtitle gaps '
                          f'({stats["total_gap_duration_s"]:.0f}s total), '
                          f'speech: {stats["speech_pct"]:.0f}%',
                          file=sys.stderr)
                else:
                    print(f'  [{ep}] No missing subtitles detected '
                          f'(speech: {stats["speech_pct"]:.0f}%)',
                          file=sys.stderr)

                # Cache speech timeline for Phase 2 reuse
                if cache_path and speech_segs:
                    _save_vad_cache(cache_path, speech_segs, stats)

        finally:
            import shutil
            shutil.rmtree(vad_tmp, ignore_errors=True)

        print(f'[VAD scan] {vad_episodes_scanned} episodes scanned, '
              f'{vad_total_gaps} missing-subtitle gaps in '
              f'{len(all_missing_subtitles)} episodes',
              file=sys.stderr)
    elif video_dir and skip_vad:
        print(f'[VAD scan] --skip-vad: speech detection disabled',
              file=sys.stderr)

    # 构建摘要 (v5.0)
    summary = {
        'files_scanned': files_scanned,
        'total_cues': total_cues,
        'garbled_count': len(all_garbled),
        'repeat_count': len(all_repeats),
        'term_count': len(all_terms),
        'episodes_with_issues': len(all_issues),
        # v5.0: VAD missing subtitle stats
        'vad_episodes_scanned': vad_episodes_scanned,
        'missing_subtitle_gaps': vad_total_gaps,
        'episodes_with_missing_subs': len(all_missing_subtitles),
    }

    return {
        'garbled_cues': all_garbled,
        'missing_subtitles': all_missing_subtitles,   # v5.0
        'per_episode_issues': dict(all_issues),
        'repeats': all_repeats,
        'term_frequencies': dict(all_terms),
        'gap_statistics': all_gap_stats,              # v5.0
        'summary': summary,
    }


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════

def _save_vad_cache(cache_path, speech_segs, stats):
    """Save VAD speech timeline to JSON for Phase 2 reuse."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump({
                'speech_segs': speech_segs,
                'stats': stats,
            }, f, ensure_ascii=False)
    except Exception as e:
        print(f'[VAD cache] Failed to save {cache_path}: {e}', file=sys.stderr)

# ═══════════════════════════════════════════════════════════════
# 输出函数
# ═══════════════════════════════════════════════════════════════

def write_findings_json(output, path):
    """将扫描结果写入 findings JSON。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def write_issues(output, issues_dir):
    """将 per-episode issues 写入独立 JSON 文件。"""
    os.makedirs(issues_dir, exist_ok=True)
    for ep, issues in output['per_episode_issues'].items():
        # 跳过无法识别的条目（空或占位符）
        if not ep or ep == '???':
            print(f'  跳过无效ID: {ep} ({len(issues)} issues)', file=sys.stderr)
            continue
        path = os.path.join(issues_dir, f'issues_{ep}.json')
        data = {
            'episode': ep,
            'issue_count': len(issues),
            'issues': issues,
            'source': 'unified_scanner.py',
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    setup_windows_utf8()
    parser = argparse.ArgumentParser(
        description='统一字符扫描器 — 字符残留 + VAD 有人声无字幕检测',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础扫描
  python unified_scanner.py --target-dir ./AI审查后/ --output-findings findings.json

  # 完整输出（findings + per-episode issues）
  python unified_scanner.py --target-dir ./AI审查后/ \\
    --output-findings findings.json \\
    --output-issues issues/

  # VAD 有人声无字幕检测（需视频文件）
  python unified_scanner.py --target-dir ./AI审查后/ \\
    --video-dir ./video/ --output-findings findings.json

  # 保留 OP/ED 区域（不跳过）
  python unified_scanner.py --target-dir ./AI审查后/ --no-skip-oped
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--output-findings', help='Findings JSON 输出路径')
    parser.add_argument('--output-issues', help='Per-episode issues 输出目录')
    parser.add_argument('--no-skip-oped', action='store_true',
                        help='不跳过 OP/ED 区域（默认跳过开头 95s + 结尾 120s）')
    parser.add_argument('--build-glossary', action='store_true',
                        help='扫描完成后自动生成术语表 proper-nouns.md')
    parser.add_argument('--glossary-output', default=None,
                        help='术语表输出路径（默认: reports/proper-nouns.md）')
    parser.add_argument('--ai-nouns', default=None,
                        help='AI WebSearch 补充的专名 JSON 路径')
    parser.add_argument('--project-lang', default='ja',
                        help='目标语言代码（ja=日语，zh=中文）。影响乱码判断逻辑。')
    parser.add_argument('--format', choices=['srt', 'ass', 'auto'], default='auto',
                        help='字幕格式（默认自动检测）')
    # v5.0: VAD 有人声无字幕检测
    parser.add_argument('--video-dir', default=None,
                        help='视频目录（启用 VAD 有人声无字幕检测）')
    parser.add_argument('--skip-vad', action='store_true',
                        help='跳过 VAD 检测（即使有 --video-dir）')
    parser.add_argument('--vad-cache-dir', default=None,
                        help='VAD speech timeline 缓存目录（默认 temp/scans/）')
    parser.add_argument('--episodes', '-e', default=None,
                        help='限定扫描的集数: EP001-EP010, EP001,EP005, 1-10')
    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f'错误: 目录不存在: {args.target_dir}', file=sys.stderr)
        sys.exit(1)

    # 扫描
    print(f'扫描目录: {args.target_dir}', file=sys.stderr)
    if args.video_dir:
        print(f'VAD 检测: 启用 (视频目录: {args.video_dir})', file=sys.stderr)

    # Parse episode filter
    episodes = None
    if args.episodes:
        episodes = _parse_episodes(args.episodes)
        print(f'限定集数: {len(episodes)} 集 ({episodes[0]}–{episodes[-1]})'
              if len(episodes) > 1 else f'限定集数: {episodes[0]}',
              file=sys.stderr)

    result = scan_all(args.target_dir, skip_oped=not args.no_skip_oped,
                      target_lang=args.project_lang,
                      video_dir=args.video_dir, skip_vad=args.skip_vad,
                      vad_cache_dir=args.vad_cache_dir,
                      episodes=episodes)
    s = result['summary']

    # 摘要输出到 stderr
    print(f'\n=== 扫描完成 ===', file=sys.stderr)
    print(f'文件: {s["files_scanned"]} | Cues: {s["total_cues"]} | '
          f'Garbled: {s["garbled_count"]} | Repeats: {s.get("repeat_count", 0)}',
          file=sys.stderr)
    if s.get('missing_subtitle_gaps', 0) > 0:
        print(f'有人声无字幕: {s["missing_subtitle_gaps"]} gaps '
              f'in {s.get("episodes_with_missing_subs", 0)} episodes',
              file=sys.stderr)
    print(file=sys.stderr)

    if s['episodes_with_issues']:
        print(f'需 Whisper 的集数: {s["episodes_with_issues"]}', file=sys.stderr)

    # 写入输出文件
    if args.output_findings:
        write_findings_json(result, args.output_findings)
        print(f'\n→ findings: {args.output_findings}', file=sys.stderr)

    if args.output_issues:
        write_issues(result, args.output_issues)
        count = len(result['per_episode_issues'])
        print(f'→ issues: {args.output_issues}/ ({count} 集)', file=sys.stderr)

    # 如果没有指定任何输出，则输出 findings 到 stdout
    if not any([args.output_findings, args.output_issues]):
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')

    # 自动生成术语表
    if args.build_glossary and args.output_findings:
        # Default: reports/proper-nouns.md (from CWD, not temp/scans/)
        glossary_out = args.glossary_output or os.path.join(
            os.getcwd(), 'reports', 'proper-nouns.md')
        print(f'\n→ 生成术语表: {glossary_out}', file=sys.stderr)
        import subprocess
        build_script = os.path.join(lib._path.SCRIPTS_DIR, 'nouns', 'build_glossary.py')
        cmd = [
            sys.executable, build_script,
            '--findings', args.output_findings,
            '--output', glossary_out,
            '--lang', args.project_lang,
        ]
        if getattr(args, 'ai_nouns', None) and os.path.exists(args.ai_nouns):
            cmd.extend(['--ai-nouns', args.ai_nouns])
        subprocess.run(cmd, check=False)
        print(f'→ 术语表完成', file=sys.stderr)


if __name__ == '__main__':
    main()
