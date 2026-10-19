#!/usr/bin/env python3
"""Gate a translation run: kana residue, echo, leftover variants, CPS.

This is the "did the normalization actually work" check, meant to run after
``translate_srt`` and ``normalize_variants``.  Thresholds default to 0 for the
three hard errors; CPS is a soft limit (default 12 chars/sec, same as QA) and
counts toward the exit code only when its threshold is set.

Exit codes: 0 pass, 1 fail (threshold exceeded), 2 bad arguments.

Usage:
    python eval/text_gate.py --zh-dir translated/zh \
        --glossary glossaries/sabu.series.json \
        --meta glossaries/sabu.series.meta.json \
        --source-dir 日语参考字幕      # optional: enables echo detection
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import lib._path  # noqa: F401,E402

from lib.glossary import Normalizer, build_variant_map, load_glossary, load_meta  # noqa: E402
from lib.subtitle_io import read_subtitles  # noqa: E402
from lib.translation.qa import count_kana, cue_violations, is_untranslated  # noqa: E402


def _episode(path: str) -> str:
    return os.path.basename(path)[:-4]


def check_file(zh_path: str, *, source_path: str = '', normalizer=None,
               max_chars: int = 20, max_cps: float = 12.0) -> dict:
    cues = read_subtitles(zh_path, mark_garbled=False)
    src = read_subtitles(source_path, mark_garbled=False) if (
        source_path and os.path.isfile(source_path)) else []
    kana = echo = variants = cps = 0
    bad = []
    for i, cue in enumerate(cues):
        text = str(cue.get('text') or '')
        reasons = []
        if count_kana(text):
            kana += 1
            reasons.append('kana')
        if src and i < len(src) and is_untranslated(
                str(src[i].get('text') or ''), text):
            echo += 1
            reasons.append('echo')
        if normalizer is not None and normalizer.has_rules:
            _, hits = normalizer.normalize(text)
            if hits:
                variants += 1
                reasons.append('variant:' + ','.join(
                    sorted({h['variant'] for h in hits})))
        violations = cue_violations(cue, max_chars=max_chars, max_cps=max_cps)
        if any(v['rule'] == 'max_cps' for v in violations):
            cps += 1
            reasons.append('cps')
        if reasons:
            bad.append({'cue': i + 1, 'start': cue.get('start'),
                        'reasons': reasons, 'text': text[:60]})
    return {'episode': _episode(zh_path), 'cues': len(cues), 'kana': kana,
            'echo': echo, 'variants': variants, 'cps': cps,
            'items': bad}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zh-dir', required=True)
    parser.add_argument('--glossary', default='')
    parser.add_argument('--meta', default='')
    parser.add_argument('--source-dir', default='')
    parser.add_argument('--max-kana', type=int, default=0)
    parser.add_argument('--max-echo', type=int, default=0)
    parser.add_argument('--max-variants', type=int, default=0)
    parser.add_argument('--max-cps', type=int, default=-1,
                        help='allowed CPS-violating cues (-1 = do not gate)')
    parser.add_argument('--max-chars', type=int, default=20)
    parser.add_argument('--cps-limit', type=float, default=12.0)
    parser.add_argument('--report', default='')
    args = parser.parse_args(argv)

    normalizer = None
    if args.glossary:
        normalizer = Normalizer(build_variant_map(
            load_glossary(args.glossary), load_meta(args.meta)))

    files = sorted(glob.glob(os.path.join(args.zh_dir, '*.srt')))
    if not files:
        print(f'[gate] no subtitles in {args.zh_dir}', file=sys.stderr)
        return 2

    results = []
    for path in files:
        source_path = os.path.join(args.source_dir, _episode(path) + '.srt') \
            if args.source_dir else ''
        results.append(check_file(
            path, source_path=source_path, normalizer=normalizer,
            max_chars=args.max_chars, max_cps=args.cps_limit))

    totals = {key: sum(r[key] for r in results)
              for key in ('kana', 'echo', 'variants', 'cps')}
    failures = []
    if totals['kana'] > args.max_kana:
        failures.append(f"kana {totals['kana']}>{args.max_kana}")
    if totals['echo'] > args.max_echo:
        failures.append(f"echo {totals['echo']}>{args.max_echo}")
    if totals['variants'] > args.max_variants:
        failures.append(f"variants {totals['variants']}>{args.max_variants}")
    if args.max_cps >= 0 and totals['cps'] > args.max_cps:
        failures.append(f"cps {totals['cps']}>{args.max_cps}")

    print(f'[gate] {len(results)} episode(s): kana {totals["kana"]}, '
          f'echo {totals["echo"]}, variants {totals["variants"]}, '
          f'cps {totals["cps"]}', file=sys.stderr)
    for result in results:
        if any(result[k] for k in ('kana', 'echo', 'variants')):
            print(f"  {result['episode']}: kana {result['kana']}, "
                  f"echo {result['echo']}, variants {result['variants']}",
                  file=sys.stderr)

    payload = {'totals': totals, 'thresholds': {
        'kana': args.max_kana, 'echo': args.max_echo,
        'variants': args.max_variants, 'cps': args.max_cps},
        'failures': failures, 'files': results}
    if args.report:
        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        with open(args.report, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write('\n')

    if failures:
        print('[gate] FAIL: ' + '; '.join(failures), file=sys.stderr)
        return 1
    print('[gate] PASS', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
