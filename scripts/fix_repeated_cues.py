#!/usr/bin/env python3
"""Whisper 重复检测 + 切片修复 — 独立后处理脚本。

检测连续高度相似的 cue（Whisper 幻觉重复），切片音频重跑 Whisper 修复。

使用 difflib 序列匹配比（默认阈值 0.85），精准捕获"仅差 1-2 个假名"的幻觉重复，
避免自然对话共享词汇的长句被误判。

Usage:
  # 单集
  python fix_repeated_cues.py EP001.srt --video "EP001.mkv" --lang ja --dry-run

  # 批量
  python fix_repeated_cues.py --input-dir "日语参考字幕" \
    --video-dir "D:/Video/..." --lang ja

  # 预览（不改文件）
  python fix_repeated_cues.py --input-dir "日语参考字幕" \
    --video-dir "D:/Video/..." --lang ja --dry-run
"""

import argparse
import difflib
import os
import sys
import tempfile

import lib._path  # noqa: F401

from lib.whisper_utils import (
    setup_windows_utf8, extract_audio_wav, run_whisper,
    filter_low_confidence, format_tc, write_srt, parse_srt, to_seconds,
)
from lib.project_utils import find_video
from lib.config import WHISPER_CLI, WHISPER_MODEL

setup_windows_utf8()


# ═══════════════════════════════════════════════════════════════
# 相似度检测
# ═══════════════════════════════════════════════════════════════

def _char_set(text):
    """Extract meaningful character set (kana + kanji + alphanumeric)."""
    chars = set()
    for ch in text.strip():
        if ch.isalnum() or '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff':
            chars.add(ch)
    return chars


def text_similarity(a, b):
    """Edit-distance based similarity: difflib SequenceMatcher ratio.

    Catches "only 1-2 kana different" Whisper hallucinations:
    - "ごめん" / "ごめん" → 1.0
    - "私に何のご用ですか" / "私に何のご用ですが" → ~0.95 (1 char diff)
    - "お前はいつまでも..." / "いつまでも子どもの..." → ~0.55 (different sentences)

    Avoids false positives where different dialogue shares vocabulary.
    """
    return difflib.SequenceMatcher(None, a.strip(), b.strip()).ratio()


def detect_similar_groups(cues, similarity_threshold=0.85, min_group_size=2):
    """Detect clusters of consecutive highly-similar cues.

    Sliding-window scan: adjacent cues with similarity >= threshold are
    grouped together. Returns list of index-groups.

    Args:
        cues: cue list (needs 'text', 'start_s', 'end_s')
        similarity_threshold: Jaccard/sequence similarity threshold (default 0.6)
        min_group_size: minimum consecutive similar cues to trigger (default 2)

    Returns:
        [[idx, idx, ...], ...]  each sublist is one repeated group
    """
    if len(cues) < min_group_size:
        return []

    groups = []
    current = []

    for i in range(len(cues) - 1):
        sim = text_similarity(cues[i]['text'], cues[i + 1]['text'])
        if sim >= similarity_threshold:
            if not current:
                current.append(i)
            current.append(i + 1)
        else:
            if len(current) >= min_group_size:
                groups.append(sorted(set(current)))
            current = []

    if len(current) >= min_group_size:
        groups.append(sorted(set(current)))

    # Merge overlapping/adjacent groups
    if len(groups) <= 1:
        return groups

    merged = [groups[0]]
    for g in groups[1:]:
        last = merged[-1]
        if g[0] <= last[-1] + 1:
            merged[-1] = sorted(set(last + g))
        else:
            merged.append(g)

    return merged


# ═══════════════════════════════════════════════════════════════
# 切片修复
# ═══════════════════════════════════════════════════════════════

def fix_group(group_indices, cues, video_path, lang='ja',
              context_window=1, dry_run=False):
    """Slice audio, re-run Whisper, replace original cues for a repeated group.

    Args:
        group_indices: list of cue indices in the group
        cues: full cue list
        video_path: video file path
        lang: language code
        context_window: number of surrounding normal cues to include as audio context
        dry_run: preview only

    Returns:
        (fixed_cues, report_dict)
    """
    first_idx = group_indices[0]
    last_idx = group_indices[-1]

    # Audio slice range (with context padding)
    ctx_start_idx = max(0, first_idx - context_window)
    ctx_end_idx = min(len(cues) - 1, last_idx + context_window)

    ss = cues[ctx_start_idx]['start_s']
    end_s = cues[ctx_end_idx]['end_s']
    duration = end_s - ss

    print(f'    [{cues[first_idx]["start"]} -> {cues[last_idx]["end"]}] '
          f'{len(group_indices)} cues, similarity detected')

    if dry_run:
        for idx in group_indices:
            c = cues[idx]
            n = c.get('index', idx + 1)
            print(f'      #{n} {c["start"]} {c["text"][:60]}')
        return cues, {'dry_run': True, 'indices': group_indices}

    # Slice audio + re-run Whisper
    tmpdir = tempfile.mkdtemp()
    slice_path = os.path.join(tmpdir, 'slice.wav')
    try:
        extract_audio_wav(video_path, slice_path, ss=ss, duration=duration)
    except Exception as e:
        print(f'    [WARN] audio extraction failed: {e}')
        return cues, {'error': str(e)}

    segs = run_whisper(slice_path, WHISPER_CLI, WHISPER_MODEL, language=lang)
    kept, discarded = filter_low_confidence(segs)

    # Map Whisper output back to absolute timeline
    new_cues = []
    for seg in kept:
        text = seg['text'].strip()
        if not text:
            continue
        new_cues.append({
            'start_s': ss + seg['start_s'],
            'end_s': ss + seg['end_s'],
            'text': text,
        })

    # Check if re-transcription still shows similarity
    still_similar = False
    if len(new_cues) >= 2:
        sim_count = 0
        for i in range(len(new_cues) - 1):
            if text_similarity(new_cues[i]['text'], new_cues[i + 1]['text']) >= 0.6:
                sim_count += 1
        still_similar = sim_count >= len(new_cues) * 0.5

    # Build replacement
    before = cues[:first_idx]
    after = cues[last_idx + 1:]

    if still_similar:
        print(f'    [WARN] re-transcription still shows similarity '
              f'({len(new_cues)} cues) - keeping original, may be instrumental')
        result = before + cues[first_idx:last_idx + 1] + after
    elif len(new_cues) == 0:
        print(f'    -> Whisper returned 0 segments, deleting {len(group_indices)} cues')
        result = before + after
    else:
        print(f'    -> replaced with {len(new_cues)} cues')
        for c in new_cues:
            c['start'] = format_tc(c['start_s'])
            c['end'] = format_tc(c['end_s'])
        result = before + new_cues + after

    # Cleanup
    try:
        os.unlink(slice_path)
        os.rmdir(tmpdir)
    except OSError:
        pass

    # Renumber
    for i, c in enumerate(result, 1):
        c['index'] = i

    return result, {
        'original_count': len(group_indices),
        'new_count': len(new_cues),
        'still_similar': still_similar,
    }


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def process_one(srt_path, video_path, lang='ja', similarity_threshold=0.85,
                min_group_size=2, dry_run=False):
    """Process a single SRT file. Returns (changed, reports)."""
    cues = parse_srt(srt_path, mark_garbled=False)
    if not cues:
        print(f'  empty file, skip')
        return False, []

    for c in cues:
        if 'start_s' not in c:
            c['start_s'] = to_seconds(c['start'])
        if 'end_s' not in c:
            c['end_s'] = to_seconds(c['end'])

    groups = detect_similar_groups(cues, similarity_threshold, min_group_size)
    if not groups:
        return False, []

    print(f'  {len(groups)} similar group(s) found '
          f'(threshold={similarity_threshold}, min_group={min_group_size})')

    total_fixed = 0
    reports = []
    for indices in groups:
        cues, report = fix_group(indices, cues, video_path, lang,
                                 dry_run=dry_run)
        reports.append(report)
        if not dry_run and 'error' not in report:
            total_fixed += report.get('original_count', 0)

    if not dry_run and total_fixed > 0:
        write_srt(srt_path, cues)
        print(f'  -> written: {len(cues)} cues ({total_fixed} fixed)')

    return total_fixed > 0, reports


def main():
    parser = argparse.ArgumentParser(
        description='Detect & fix Whisper repetition hallucination in SRT files')
    parser.add_argument('srt', nargs='?', help='Single SRT file to fix')
    parser.add_argument('--video', help='Video file (required for single-file mode)')
    parser.add_argument('--input-dir', help='Directory of SRT files (batch mode)')
    parser.add_argument('--video-dir', help='Video directory (batch mode)')
    parser.add_argument('--lang', default='ja', help='Language (default: ja)')
    parser.add_argument('--similarity', type=float, default=0.85,
                        help='Similarity threshold (default: 0.85)')
    parser.add_argument('--min-group', type=int, default=2,
                        help='Min consecutive similar cues to trigger (default: 2)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, do not modify files')
    args = parser.parse_args()

    if not WHISPER_CLI or not WHISPER_MODEL:
        print('ERROR: WHISPER_CLI and WHISPER_MODEL env vars required.',
              file=sys.stderr)
        sys.exit(1)

    # Single file mode
    if args.srt:
        if not args.video:
            print('ERROR: --video required for single-file mode', file=sys.stderr)
            sys.exit(1)
        print(f'{os.path.basename(args.srt)}:')
        changed, reports = process_one(
            args.srt, args.video, args.lang,
            args.similarity, args.min_group, args.dry_run)
        if not changed:
            print('  no similar groups found')
        sys.exit(0)

    # Batch mode
    if not args.input_dir or not args.video_dir:
        print('ERROR: --input-dir and --video-dir required for batch mode',
              file=sys.stderr)
        sys.exit(1)

    srt_files = sorted(f for f in os.listdir(args.input_dir)
                       if f.endswith('.srt'))
    if not srt_files:
        print(f'No SRT files found in {args.input_dir}')
        sys.exit(0)

    from lib.whisper_utils import extract_ep_number

    total_changed = 0
    total_groups = 0
    for fname in srt_files:
        srt_path = os.path.join(args.input_dir, fname)

        ep = extract_ep_number(fname)
        video_path = find_video(os.getcwd(), ep, args.video_dir) if ep != '???' else None
        if not video_path:
            print(f'{fname}: video not found, skip')
            continue

        print(f'{fname}:')
        changed, reports = process_one(
            srt_path, video_path, args.lang,
            args.similarity, args.min_group, args.dry_run)
        if changed:
            total_changed += 1
            total_groups += len(reports)
        elif not reports:
            print('  no similar groups found')

    print(f'\nDone: {len(srt_files)} files scanned, '
          f'{total_changed} changed ({total_groups} groups)')


if __name__ == '__main__':
    main()
