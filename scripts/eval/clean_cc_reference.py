#!/usr/bin/env python3
"""Strip Japanese closed-caption annotations from reference SRTs.

Japanese WEBRip CC (`*.ja[cc].srt`) is human-authored but carries
non-dialogue material that ASR never emits:

  （ナレーション）その年の夏は          -> その年の夏は
  （八神太一(やがみたいち)）ん… ん？    -> ん… ん？
  東南アジアでは全く雨が降(ふ)らず      -> 東南アジアでは全く雨が降らず
  ♪～ / （効果音）/ （お腹の鳴る音）    -> dropped

Use the cleaned copy for CER / alignment; raw files are never modified.

Usage:
  python scripts/eval/clean_cc_reference.py \
      --input-dir "<video-root>/数码宝贝/日语字幕" \
      --output-dir temp/references/digimon --report temp/references/cc.json
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lib._path  # noqa: F401,E402
from lib.whisper_utils import parse_srt, write_srt  # noqa: E402

# Paired full-width annotation: speaker, narration, sound effect, etc.
FULLWIDTH_TAG = re.compile(r'（[^）]*）')
# Half-width parenthetical reading, e.g. 降(ふ)らず / 洪水(こうずい).
FURIGANA = re.compile(r'\([ぁ-ゟ]+\)')
# Music notes used as line markers.
MUSIC = re.compile(r'[♪♫♬]+')
# Anything left after stripping must contain at least one letter/kana/kanji.
VISIBLE = re.compile(r'[0-9A-Za-z぀-ヿ一-鿿]')


def clean_text(text: str) -> str:
    """Remove CC annotations from one cue; return '' when nothing is left."""
    lines = []
    for raw_line in (text or '').split('\n'):
        line = FULLWIDTH_TAG.sub('', raw_line)
        line = FURIGANA.sub('', line)
        line = MUSIC.sub('', line).strip()
        if line and VISIBLE.search(line):
            lines.append(line)
    return '\n'.join(lines).strip()


def clean_cues(cues: list[dict]) -> tuple[list[dict], dict]:
    """Clean every cue, dropping cues that become empty.

    Returns (cleaned_cues, stats).  Timing is preserved verbatim.
    """
    out = []
    dropped = 0
    for cue in cues:
        text = clean_text(cue.get('text', ''))
        if not text:
            dropped += 1
            continue
        new_cue = dict(cue)
        new_cue['text'] = text
        out.append(new_cue)
    return out, {'cues_in': len(cues), 'cues_out': len(out),
                 'dropped': dropped}


def clean_files(input_dir: str, output_dir: str, pattern: str = '*.srt',
                dry_run: bool = False) -> list[dict]:
    """Clean every matching SRT; return per-file stats."""
    in_dir = Path(input_dir).resolve()
    out_dir = Path(output_dir).resolve()
    if not in_dir.is_dir():
        raise FileNotFoundError(f'input dir not found: {input_dir}')
    if in_dir == out_dir:
        raise ValueError('output-dir must differ from input-dir '
                         '(raw CC files are never modified)')
    paths = sorted(glob.glob(str(in_dir / pattern)))
    if not paths:
        # Users often pass literal `*.ja[cc].srt`; brackets are glob
        # character classes.  Escape only bracket characters so `*` still
        # works as a wildcard.
        escaped = pattern.replace('[', '[[]')
        paths = sorted(glob.glob(str(in_dir / escaped)))
    if not paths:
        raise FileNotFoundError(f'no {pattern!r} in {input_dir}')

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for path in paths:
        cues = parse_srt(path, mark_garbled=False)
        cleaned, stats = clean_cues(cues)
        out_path = out_dir / Path(path).name
        if not dry_run and cleaned:
            write_srt(str(out_path), cleaned)
        rows.append({
            'file': Path(path).name,
            'output': '' if dry_run else str(out_path),
            **stats,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Strip Japanese CC annotations from reference SRTs')
    ap.add_argument('--input-dir', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--pattern', default='*.srt')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', default='', help='optional JSON report path')
    args = ap.parse_args(argv)

    try:
        rows = clean_files(args.input_dir, args.output_dir, args.pattern,
                           dry_run=args.dry_run)
    except (FileNotFoundError, ValueError) as exc:
        print(f'[cc] {exc}', file=sys.stderr)
        return 2

    total_in = sum(r['cues_in'] for r in rows)
    total_out = sum(r['cues_out'] for r in rows)
    total_drop = sum(r['dropped'] for r in rows)
    empty = [r['file'] for r in rows if r['cues_out'] == 0]
    for row in rows:
        print(f"[cc] {row['file']}: {row['cues_in']} -> {row['cues_out']} "
              f"(drop {row['dropped']})", file=sys.stderr)
    print(f'[cc] {len(rows)} files: {total_in} -> {total_out} cues '
          f'(drop {total_drop})', file=sys.stderr)
    if empty:
        print(f'[cc] WARN empty after cleaning: {", ".join(empty)}',
              file=sys.stderr)

    payload = {
        'input_dir': str(Path(args.input_dir).resolve()),
        'output_dir': '' if args.dry_run else str(Path(args.output_dir).resolve()),
        'pattern': args.pattern,
        'dry_run': args.dry_run,
        'summary': {'files': len(rows), 'cues_in': total_in,
                    'cues_out': total_out, 'dropped': total_drop,
                    'empty_files': empty},
        'files': rows,
    }
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'[cc] wrote {args.report}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
