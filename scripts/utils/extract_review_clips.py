#!/usr/bin/env python3
"""Extract video clips for manual review of unfixable subtitle issues.

Collects unfixable items from Whisper (confidence=none), report layer 6 (人工交付),
and noun_checker unresolved items → extracts short video clips via ffmpeg →
generates review-checklist.md for human reviewer.

Usage:
  # From whisper fixes.json
  python extract_review_clips.py --fixes temp/scans/EP001_fixes.json \\
      --video-dir "E:/Animation/TV/..." --srt-dir AI审查后/ \\
      --output reports/manual-review/

  # From report layer 6 (人工交付)
  python extract_review_clips.py --report reports/问题解决报告.md --step 6 \\
      --video-dir "E:/Animation/TV/..." --srt-dir AI审查后/ \\
      --output reports/manual-review/

  # From noun_check unresolved
  python extract_review_clips.py --noun-check temp/scans/noun_check.json \\
      --video-dir "E:/Animation/TV/..." --output reports/manual-review/

  # All sources combined
  python extract_review_clips.py \\
      --fixes temp/scans/*_fixes.json \\
      --report reports/问题解决报告.md --step 6 \\
      --noun-check temp/scans/noun_check.json \\
      --video-dir "E:/Animation/TV/..." --srt-dir AI审查后/ \\
      --output reports/manual-review/
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

# UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)
# Add both utils/ and scripts/ to path
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.whisper_utils import to_seconds


# ═══════════════════════════════════════════════════════════════
# Collect unfixable items
# ═══════════════════════════════════════════════════════════════

def collect_from_fixes(fixes_path):
    """Extract confidence=none items from whisper fixes.json."""
    if not os.path.exists(fixes_path):
        print(f'  [skip] {fixes_path} not found', file=sys.stderr)
        return []

    with open(fixes_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    items = []
    srt_name = data.get('srt', '')
    ep_match = re.search(r'(\d{3})', srt_name)
    ep = f'EP{ep_match.group(1)}' if ep_match else ''

    for fix in data.get('fixes', []):
        if fix.get('confidence') == 'none':
            items.append({
                'ep': ep,
                'start': fix.get('start', ''),
                'end': fix.get('end', ''),
                'original': fix.get('original', ''),
                'source': 'Whisper unfixable',
            })

    print(f'  [{os.path.basename(fixes_path)}] {len(items)} unfixable', file=sys.stderr)
    return items


def collect_from_report(report_path, step='6'):
    """Extract ⬜ pending items from 问题解决报告.md Layer 6 (人工交付)."""
    if not os.path.exists(report_path):
        print(f'  [skip] {report_path} not found', file=sys.stderr)
        return []

    from update_report import read_report

    data = read_report(report_path)
    entries = data.get(step, [])
    items = []
    for e in entries:
        if e.get('status') == '⬜':
            items.append({
                'ep': e.get('ep', ''),
                'start': e.get('time', ''),
                'end': '',
                'original': e.get('original', ''),
                'source': f'Report step {step}',
            })

    print(f'  [report step {step}] {len(items)} pending', file=sys.stderr)
    return items


def collect_from_noun_check(noun_path):
    """Extract unknown/mismatch items from noun_checker output."""
    if not os.path.exists(noun_path):
        print(f'  [skip] {noun_path} not found', file=sys.stderr)
        return []

    with open(noun_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    items = []
    for r in data.get('results', []):
        if r.get('status') in ('unknown', 'mismatch'):
            items.append({
                'ep': r.get('ep', ''),
                'start': r.get('start', ''),
                'end': r.get('end', ''),
                'original': f'{r.get("candidate", "")} → {r.get("expected", "?")}',
                'source': 'Noun checker',
            })

    print(f'  [{os.path.basename(noun_path)}] {len(items)} unresolved nouns',
          file=sys.stderr)
    return items


# ═══════════════════════════════════════════════════════════════
# Video clip extraction
# ═══════════════════════════════════════════════════════════════

def find_video(episode, video_dir):
    """Find the video file for an episode."""
    ep_num = episode[2:] if episode.startswith('EP') else episode
    exts = ('.mkv', '.mp4', '.avi', '.mov')
    for fname in sorted(os.listdir(video_dir)):
        if ep_num in fname and fname.lower().endswith(exts):
            return os.path.join(video_dir, fname)
    return None


def extract_clip(video_path, start_s, end_s, output_path, padding=3.0):
    """Extract a short video clip with padding around the timestamp.

    Args:
        video_path: full path to video file
        start_s: start time in seconds
        end_s: end time in seconds (if 0 or unknown, uses start_s + 5s)
        output_path: output mp4 path
        padding: seconds to add before and after (default 3s)

    Returns:
        True on success, False on failure
    """
    clip_start = max(0, start_s - padding)
    duration = max(end_s - start_s, 2.0) + 2 * padding if end_s > start_s else 5.0 + 2 * padding

    # Ensure duration is reasonable (2-30 seconds)
    duration = max(2.0, min(duration, 30.0))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-ss', str(clip_start),
        '-t', str(duration),
        '-i', video_path,
        '-c:v', 'libx264', '-crf', '28',
        '-c:a', 'aac', '-b:a', '64k',
        '-vf', 'scale=640:-2',
        '-movflags', '+faststart',
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        return True
    except subprocess.TimeoutExpired:
        print(f'  TIMEOUT: {output_path}', file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f'  FFMPEG ERROR: {e.stderr[-200:] if e.stderr else "unknown"}', file=sys.stderr)
    except Exception as e:
        print(f'  ERROR: {e}', file=sys.stderr)
    return False


# ═══════════════════════════════════════════════════════════════
# Checklist generation
# ═══════════════════════════════════════════════════════════════

CHECKLIST_HEADER = """# 人工审查清单
> 导出: {date}
> 共 {count} 条待审查
>
> **填写方法**：每条下方「修正:」后直接写正确台词（可多行），
> 脚本会用 Whisper VAD 自动分配时间轴。
>
> 填写完毕后运行:
>   python scripts/utils/extract_review_clips.py --apply-checklist <checklist.md> --srt-dir <DIR>
>
---
"""

CHECKLIST_ENTRY = """{ep} | {start} ~ {end} | {clip_file}
来源: {source}
残留: {original}
修正:

---
"""


def generate_checklist(items, output_dir, video_dir, padding=3.0):
    """Extract video clips and generate review-checklist.md.

    Args:
        items: [{'ep': 'EP001', 'start': '00:02:00.000', 'end': '...',
                  'original': 'garbled text', 'source': 'Whisper unfixable'}, ...]
        output_dir: where to write clips and checklist
        video_dir: directory containing video files
        padding: seconds of padding around each clip

    Returns:
        checklist_path
    """
    os.makedirs(output_dir, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')

    # Group by episode
    by_ep = defaultdict(list)
    for item in items:
        by_ep[item['ep']].append(item)

    # Deduplicate by (ep, start)
    seen = set()
    unique_items = []
    for item in items:
        key = (item['ep'], item['start'])
        if key not in seen:
            seen.add(key)
            unique_items.append(item)

    print(f'\n[extract] {len(unique_items)} unique items across '
          f'{len(by_ep)} episodes', file=sys.stderr)

    # Extract clips
    checklist_entries = []
    extracted = 0
    skipped = 0

    for item in sorted(unique_items, key=lambda x: (x['ep'], x['start'])):
        ep = item['ep']
        start_ts = item['start']
        end_ts = item.get('end', '')

        start_s = to_seconds(start_ts)
        end_s = to_seconds(end_ts) if end_ts else start_s + 5.0

        # Safe filename: replace ':' and ','
        safe_start = start_ts.replace(':', '-').replace(',', '-').replace('.', '-')
        clip_name = f'{ep}_{safe_start}.mp4'
        clip_path = os.path.join(output_dir, clip_name)

        # Find video
        video_path = find_video(ep, video_dir)
        if not video_path:
            print(f'  [skip] {ep}: no video found', file=sys.stderr)
            skipped += 1
            continue

        # Extract clip
        if os.path.exists(clip_path):
            print(f'  [exists] {clip_name}', file=sys.stderr)
        else:
            print(f'  [extract] {ep} {start_ts} → {clip_name}', file=sys.stderr)
            ok = extract_clip(video_path, start_s, end_s, clip_path, padding=padding)
            if not ok:
                skipped += 1
                continue

        extracted += 1
        checklist_entries.append(CHECKLIST_ENTRY.format(
            ep=ep,
            start=start_ts,
            end=end_ts or '?',
            clip_file=clip_name,
            source=item.get('source', 'Unknown'),
            original=item.get('original', ''),
        ))

    # Write checklist
    checklist_path = os.path.join(output_dir, 'review-checklist.md')
    with open(checklist_path, 'w', encoding='utf-8') as f:
        f.write(CHECKLIST_HEADER.format(date=today, count=len(checklist_entries)))
        for entry in checklist_entries:
            f.write(entry)

    print(f'\n[{len(checklist_entries)} entries] → {checklist_path}', file=sys.stderr)
    print(f'  Clips extracted: {extracted}, Skipped: {skipped}', file=sys.stderr)

    return checklist_path


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Extract video clips for manual subtitle review')
    parser.add_argument('--fixes', nargs='*', default=[],
                        help='Whisper fixes.json file(s)')
    parser.add_argument('--report', help='问题解决报告.md path')
    parser.add_argument('--step', type=str, default='6',
                        help='Report layer to collect from (default: 6=人工交付)')
    parser.add_argument('--noun-check', nargs='*', default=[],
                        help='Noun checker JSON output(s)')
    parser.add_argument('--findings', help='findings.json for per-episode issues')
    parser.add_argument('--video-dir', required=True,
                        help='Directory containing video files')
    parser.add_argument('--srt-dir', help='Directory containing SRT files (for context)')
    parser.add_argument('--output', '-o', default='reports/manual-review/',
                        help='Output directory for clips and checklist')
    parser.add_argument('--padding', type=float, default=3.0,
                        help='Padding seconds around each clip (default: 3)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without extracting clips')
    args = parser.parse_args()

    # ── Collect items ──
    all_items = []

    print('[collect] Gathering unfixable items ...', file=sys.stderr)

    for fixes_path in args.fixes:
        all_items.extend(collect_from_fixes(fixes_path))

    if args.report:
        all_items.extend(collect_from_report(args.report, args.step))

    for noun_path in args.noun_check:
        all_items.extend(collect_from_noun_check(noun_path))

    if not all_items:
        print('\n✓ No unfixable items found — nothing to extract.', file=sys.stderr)
        return

    print(f'\n[total] {len(all_items)} unfixable items', file=sys.stderr)

    # ── Generate checklist ──
    if args.dry_run:
        print('\n[DRY RUN] Would extract clips for:', file=sys.stderr)
        by_ep = defaultdict(list)
        for item in all_items:
            by_ep[item['ep']].append(item)
        for ep, items in sorted(by_ep.items()):
            print(f'  {ep}: {len(items)} items', file=sys.stderr)
        print(f'\n  Output dir: {args.output}', file=sys.stderr)
    else:
        generate_checklist(all_items, args.output, args.video_dir, padding=args.padding)


if __name__ == '__main__':
    main()
