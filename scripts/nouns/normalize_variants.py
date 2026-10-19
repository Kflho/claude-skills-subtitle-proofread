#!/usr/bin/env python3
"""Normalize reviewed proper-noun variants in Chinese subtitles.

The glossary (`--glossary`) is the flat ja→zh map used by translation; the
reviewed variant table lives in `--meta` under ``aliases``
(``{canonical_zh: [observed wrong form, ...]}``).  This tool is the deterministic
half of the consistency story:

    translate --mappings ...     (glossary fixes most of it: EP051 15/15)
        → this tool              (rewrites the leftovers: old episodes, drift)
        → text_gate.py           (fails the episode if anything is left)

Dry-run is the default; it prints exactly what would change.

Usage:
    python nouns/normalize_variants.py \
        --zh-dir translated/zh \
        --glossary glossaries/sabu.series.json \
        --meta glossaries/sabu.series.meta.json \
        --dry-run            # preview (default)
    ... --apply              # write (creates .bak)
    ... --discover --ja-dir 日语参考字幕   # candidates only, never writes

Exit codes: 0 ok, 1 bad arguments/unreadable input.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

import lib._path  # noqa: F401,E402

from lib.glossary import (  # noqa: E402
    Normalizer, build_normalizer, build_variant_map, discover_variants, explain_variant_map,
    load_glossary, load_meta, normalize_cues,
)
from lib.subtitle_io import read_subtitles, write_subtitles  # noqa: E402


def _episode(path: str) -> str:
    return os.path.basename(path)[:-4] if path.lower().endswith('.srt') else os.path.basename(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zh-dir', required=True)
    parser.add_argument('--glossary', required=True)
    parser.add_argument('--meta', default='')
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--apply', action='store_true', default=False)
    parser.add_argument('--report', default='', help='write JSON summary here')
    parser.add_argument('--discover', action='store_true',
                        help='only list candidate variants (needs --ja-dir)')
    parser.add_argument('--ja-dir', default='')
    parser.add_argument('--min-count', type=int, default=2)
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        print('pick one: --dry-run or --apply', file=sys.stderr)
        return 1
    write = args.apply
    glossary = load_glossary(args.glossary)
    meta = load_meta(args.meta)

    if args.discover:
        if not args.ja_dir:
            print('--discover needs --ja-dir', file=sys.stderr)
            return 1
        ja, zh = {}, {}
        for path in sorted(glob.glob(os.path.join(args.ja_dir, '*.srt'))):
            ja[_episode(path)] = read_subtitles(path, mark_garbled=False)
        for path in sorted(glob.glob(os.path.join(args.zh_dir, '*.srt'))):
            zh[_episode(path)] = read_subtitles(path, mark_garbled=False)
        candidates = discover_variants(ja, zh, glossary,
                                       min_count=args.min_count)
        payload = {'candidates': candidates,
                   'note': 'candidates only — promote reviewed ones into '
                           '_meta.aliases, nothing was rewritten'}
        if args.report:
            with open(args.report, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        for row in candidates:
            print(f"{row['candidate']}\tpos={row['positive']}\t"
                  f"neg={row['negative']}\t{row['canonical']}",
                  file=sys.stdout)
        return 0

    variant_map = build_variant_map(glossary, meta)
    normalizer = build_normalizer(glossary, meta)
    summary = {'glossary_terms': len(glossary),
               'canonicals_with_variants': len(variant_map),
               'variants': sum(len(v) for v in variant_map.values()),
               'explain': explain_variant_map(glossary, meta),
               'files': [], 'applied': write}
    if not normalizer.has_rules:
        print('[normalize] no reviewed variants — nothing to do', file=sys.stderr)
        if args.report:
            with open(args.report, 'w', encoding='utf-8', newline='\n') as fh:
                json.dump(summary, fh, ensure_ascii=False, indent=2)
        return 0

    for path in sorted(glob.glob(os.path.join(args.zh_dir, '*.srt'))):
        cues = read_subtitles(path, mark_garbled=False)
        new_cues, changes = normalize_cues(cues, normalizer)
        if not changes:
            continue
        episode = _episode(path)
        summary['files'].append({'episode': episode, 'changes': changes})
        print(f'[normalize] {episode}: {len(changes)} cue(s)', file=sys.stderr)
        for change in changes[:10]:
            pairs = ', '.join(f"{h['variant']}→{h['canonical']}"
                              for h in change['hits'])
            print(f"  {change['start']}  {change['before'][:24]} → "
                  f"{change['after'][:24]}   [{pairs}]", file=sys.stderr)
        if write:
            shutil.copyfile(path, path + '.bak')
            write_subtitles(path, new_cues)

    total = sum(len(f['changes']) for f in summary['files'])
    print(f'[normalize] {total} cue(s) in {len(summary["files"])} file(s) '
          f'{"rewritten" if write else "would change (dry-run)"}',
          file=sys.stderr)
    if args.report:
        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        with open(args.report, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
