#!/usr/bin/env python3
"""Smart batch AI fragment fixer.

Three-tier classification:
  A: original has meaningful JP + English corruption → clean English, keep JP
  B: original is mostly noise (mj < 3) → mark __DELETE__
  C: can't determine → leave empty (needs human review)

Single-pass, no API calls.
"""

import json
import os
import re
import sys
from collections import defaultdict


def meaningful_jp_count(text):
    """Count meaningful Japanese characters (hiragana + katakana + kanji)."""
    count = 0
    for c in text:
        cp = ord(c)
        if (0x3040 <= cp <= 0x309F or   # hiragana
            0x30A0 <= cp <= 0x30FF or   # katakana
            0x4E00 <= cp <= 0x9FFF):    # kanji
            count += 1
    return count


def clean_english_fragments(text):
    """Remove isolated English/Latin fragments from Japanese text.

    "ためそうだ金庫番を選んで開け the と" → "ためそうだ金庫番を選んで開けと"
    "walk もっと強くなりたい" → "もっと強くなりたい"
    """
    # Remove standalone English words (2+ Latin chars surrounded by spaces/start/end)
    cleaned = re.sub(r'(?:^|\s)[a-zA-Z]{2,}(?:\s|$)', ' ', text)
    # Remove single Latin chars between Japanese chars or at boundaries
    cleaned = re.sub(r'(?:^|\s)[a-zA-Z](?:\s|$)', ' ', cleaned)
    # Collapse multiple spaces
    cleaned = re.sub(r'\s+', '', cleaned)
    # Remove empty
    if not cleaned.strip():
        return ''
    return cleaned


def process_fragment(frag):
    """Process one fragment. Returns (action, correction)."""
    original = frag.get('original', '').strip()
    mj = meaningful_jp_count(original)

    # ── Type B: pure noise → delete ──
    if mj < 3:
        # Check context: if context is also noisy, it's definitely noise
        ctx_before = frag.get('context_before', [])
        ctx_after = frag.get('context_after', [])
        ctx_texts = ctx_before[-2:] + ctx_after[:2]
        ctx_mj = sum(meaningful_jp_count(t) for t in ctx_texts)

        if ctx_mj >= 10:  # context has real dialogue → this is isolated noise
            return 'delete_noise', '__DELETE__'
        else:
            # Context also noisy → likely shouting/sfx scene, still noise
            return 'delete_noisy_scene', '__DELETE__'

    # ── Type A: has JP + English → clean ──
    cleaned = clean_english_fragments(original)
    if cleaned and meaningful_jp_count(cleaned) >= 2:
        # Verify cleaned text is shorter but keeps meaning
        if len(cleaned) >= 2:
            return 'cleaned', cleaned

    # ── Type C: can't determine ──
    return 'unfixable', ''


def main():
    import argparse
    parser = argparse.ArgumentParser()
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
    unfixable = []

    for fpath in json_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        ep = data['episode']
        modified = False

        for frag in data['fragments']:
            if frag.get('correction', '').strip():
                stats['already_filled'] += 1
                continue

            action, text = process_fragment(frag)
            stats[action] += 1

            if text:
                frag['correction'] = text
                modified = True
            elif action == 'unfixable':
                unfixable.append({
                    'ep': ep,
                    'time': frag['start'],
                    'original': frag['original'][:80],
                    'mj': meaningful_jp_count(frag['original']),
                    'file': fpath,
                })

        if modified and not args.dry_run:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # Summary
    total = sum(stats.values())
    print(f'\n=== Smart Fix Report ===', file=sys.stderr)
    print(f'Files: {len(json_files)}, Fragments: {total}', file=sys.stderr)
    for action, count in sorted(stats.items()):
        label = {
            'already_filled': 'Already filled (prev run)',
            'delete_noise': '🗑️  Noise → DELETE',
            'delete_noisy_scene': '🗑️  Noisy scene → DELETE',
            'cleaned': '🧹 English cleaned from JP',
            'unfixable': '⚠️  Needs manual review',
        }.get(action, action)
        print(f'  {label}: {count}', file=sys.stderr)

    if unfixable:
        print(f'\n⚠️  {len(unfixable)} still need manual review:',
              file=sys.stderr)
        for item in unfixable:
            print(f'  {item["ep"]} {item["time"]} mj={item["mj"]}: '
                  f'{item["original"][:60]}', file=sys.stderr)

    if args.dry_run:
        print(f'\n[DRY RUN] No files modified.', file=sys.stderr)


if __name__ == '__main__':
    main()
