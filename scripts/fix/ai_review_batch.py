#!/usr/bin/env python3
"""AI fragment 批量预处理 — 仅自动采纳 Whisper 已确认的猜测。

whisper_attempt 非空 = Whisper 匹配到了但被长度异常拦截 → 直接采纳。
其余所有 fragment 保持原样，留给 AI 逐条审查。

Usage:
  python ai_review_batch.py --project-dir .           # 自动采纳 whisper_attempt
  python ai_review_batch.py --project-dir . --dry-run  # 预览
"""

import json
import os
import sys
from collections import defaultdict


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='AI fragment pre-fill — auto-accept Whisper guesses only')
    parser.add_argument('--project-dir', default=os.getcwd())
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    scans_dir = os.path.join(args.project_dir, 'temp', 'scans')
    import glob
    json_files = sorted(glob.glob(os.path.join(
        scans_dir, 'ai_fragments_*.json')))

    if not json_files:
        print('No AI fragment files.', file=sys.stderr)
        return

    stats = defaultdict(int)

    for fpath in json_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        ep = data['episode']
        modified = False

        for frag in data['fragments']:
            if frag.get('correction', '').strip():
                stats['already_filled'] += 1
                continue

            whisper_guess = frag.get('whisper_attempt', '')
            if whisper_guess and whisper_guess.strip():
                frag['correction'] = whisper_guess.strip()
                stats['whisper_accepted'] += 1
                modified = True
            else:
                stats['needs_ai_review'] += 1

        if modified and not args.dry_run:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(stats.values())
    print(f'\n=== AI Fragment Pre-fill ===', file=sys.stderr)
    print(f'Files: {len(json_files)}, Fragments: {total}', file=sys.stderr)
    print(f'  Whisper guess accepted: {stats[\"whisper_accepted\"]}', file=sys.stderr)
    print(f'  Already filled:         {stats[\"already_filled\"]}', file=sys.stderr)
    print(f'  Needs AI review:        {stats[\"needs_ai_review\"]}', file=sys.stderr)

    if stats['needs_ai_review'] > 0:
        print(f'\n→ 剩余 {stats["needs_ai_review"]} 条需要 AI 逐条审查。'
              f'读 temp/scans/ai_fragments_*.json，'
              f'根据 context_before/after 判断 correction。', file=sys.stderr)

    if args.dry_run:
        print(f'\n[DRY RUN] No files modified.', file=sys.stderr)


if __name__ == '__main__':
    main()
