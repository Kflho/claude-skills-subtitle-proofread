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
import re
import sys

# 允许 `python <scripts>/eval/text_gate.py` 从任意目录直接跑（SKILL.md 就是这么写的）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402

from lib.glossary import build_normalizer, build_variant_map, load_glossary, load_meta  # noqa: E402
from lib.subtitle_io import read_subtitles  # noqa: E402
from lib.translation.qa import (  # noqa: E402
    count_kana, cue_violations, is_untranslated, strip_tags,
)


def _episode(path: str) -> str:
    return os.path.basename(path)[:-4]


LATIN = re.compile(r"[A-Za-z][A-Za-z'’.\-]{1,}")
# 西里尔残留：ru→zh 项目里最常见的失败形态——整条或半条原文没翻。
# `--max-latin` 抓不到它（西里尔不属于 [A-Za-z]），必须单独计。
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
# 汉字之间的裸空格：机翻/后处理拿空格当逗号，读起来断句全错。
# 但不是所有空格都是缺陷：歌词用空格分句、招牌把字拆开、咒语用空格隔两段。
# 只把「整条没有标点、空格承担断句作用」的算缺陷。
CJK_SPACE = re.compile(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]")
PUNCT_ANY = re.compile(r"[，。！？…、,.!?~～；：]")
LYRIC_STYLE = re.compile(r"(opening|ending|lyric|song)", re.I)


def spaced_as_punctuation(text: str) -> bool:
    """空格是否在代替标点断句。"""
    if not CJK_SPACE.search(text):
        return False
    if PUNCT_ANY.search(text):
        return False                       # 有标点，空格是排版
    runs = re.split(r"\s+", text.strip())
    if all(len(run) <= 1 for run in runs):
        return False                       # 逐字间距（招牌/标题）
    return True
CREDIT = ('字幕制作', '翻译', '时间轴', 'Society', 'Skaro', 'Hokutono')


def check_file(zh_path: str, *, source_path: str = '', normalizer=None,
               max_chars: int = 20, max_cps: float = 12.0) -> dict:
    cues = read_subtitles(zh_path, mark_garbled=False)
    src = read_subtitles(source_path, mark_garbled=False) if (
        source_path and os.path.isfile(source_path)) else []
    kana = echo = variants = cps = latin = cyrillic = cjk_space = 0
    bad = []
    for i, cue in enumerate(cues):
        raw = str(cue.get('text') or '')
        text = strip_tags(raw)      # 标签不是字幕内容，不能算进残留
        reasons = []
        # 矢量绘图（`{\p1}m 3 19 b …`）：文字检查没有意义，坐标会被当成
        # 拉丁字母和超长句。这类 cue 直接跳过。
        if re.search(r'\\p\d', raw):
            continue
        if count_kana(text):
            kana += 1
            reasons.append('kana')
        if CYRILLIC.search(text):
            cyrillic += 1
            reasons.append('cyrillic')
        style = str(((cue.get('_ass_dialogue') or {}).get('style')) or '')
        if spaced_as_punctuation(text) and not LYRIC_STYLE.search(style):
            cjk_space += 1
            reasons.append('cjk_space')
        if src and i < len(src) and is_untranslated(
                str(src[i].get('text') or ''), text):
            echo += 1
            reasons.append('echo')
        if not any(marker in text for marker in CREDIT):
            tokens = LATIN.findall(text)
            if tokens:
                latin += len(tokens)
                reasons.append('latin:' + ','.join(tokens[:3]))
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
            'echo': echo, 'variants': variants, 'cps': cps, 'latin': latin,
            'cyrillic': cyrillic, 'cjk_space': cjk_space,
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
    parser.add_argument('--max-latin', type=int, default=-1,
                        help='allowed Latin-script tokens (-1 = do not gate)')
    parser.add_argument('--max-cyrillic', type=int, default=-1,
                        help='allowed cues containing Cyrillic (-1 = do not gate)')
    parser.add_argument('--max-cjk-space', type=int, default=-1,
                        help='allowed cues with a bare space between CJK '
                             'chars (-1 = do not gate)')
    parser.add_argument('--max-cps', type=int, default=-1,
                        help='allowed CPS-violating cues (-1 = do not gate)')
    parser.add_argument('--max-chars', type=int, default=20)
    parser.add_argument('--cps-limit', type=float, default=12.0)
    parser.add_argument('--report', default='')
    args = parser.parse_args(argv)

    normalizer = None
    if args.glossary:
        normalizer = build_normalizer(load_glossary(args.glossary),
                                      load_meta(args.meta))

    files = sorted(glob.glob(os.path.join(args.zh_dir, '*.srt'))
                   + glob.glob(os.path.join(args.zh_dir, '*.ass')))
    if not files:
        print(f'[gate] no subtitles in {args.zh_dir}', file=sys.stderr)
        return 2

    results = []
    for path in files:
        source_path = os.path.join(args.source_dir,
                                   _episode(path) + os.path.splitext(path)[1]) \
            if args.source_dir else ''
        results.append(check_file(
            path, source_path=source_path, normalizer=normalizer,
            max_chars=args.max_chars, max_cps=args.cps_limit))

    totals = {key: sum(r[key] for r in results)
              for key in ('kana', 'echo', 'variants', 'cps', 'latin',
                          'cyrillic', 'cjk_space')}
    failures = []
    if totals['kana'] > args.max_kana:
        failures.append(f"kana {totals['kana']}>{args.max_kana}")
    if totals['echo'] > args.max_echo:
        failures.append(f"echo {totals['echo']}>{args.max_echo}")
    if totals['variants'] > args.max_variants:
        failures.append(f"variants {totals['variants']}>{args.max_variants}")
    if args.max_latin >= 0 and totals['latin'] > args.max_latin:
        failures.append(f"latin {totals['latin']}>{args.max_latin}")
    if args.max_cyrillic >= 0 and totals['cyrillic'] > args.max_cyrillic:
        failures.append(f"cyrillic {totals['cyrillic']}>{args.max_cyrillic}")
    if args.max_cjk_space >= 0 and totals['cjk_space'] > args.max_cjk_space:
        failures.append(f"cjk_space {totals['cjk_space']}>{args.max_cjk_space}")
    if args.max_cps >= 0 and totals['cps'] > args.max_cps:
        failures.append(f"cps {totals['cps']}>{args.max_cps}")

    print(f'[gate] {len(results)} episode(s): kana {totals["kana"]}, '
          f'echo {totals["echo"]}, variants {totals["variants"]}, '
          f'cps {totals["cps"]}, latin {totals["latin"]}, '
          f'cyrillic {totals["cyrillic"]}, cjk_space {totals["cjk_space"]}',
          file=sys.stderr)
    for result in results:
        if any(result[k] for k in ('kana', 'echo', 'variants', 'cyrillic')):
            print(f"  {result['episode']}: kana {result['kana']}, "
                  f"echo {result['echo']}, variants {result['variants']}, "
                  f"cyrillic {result['cyrillic']}",
                  file=sys.stderr)

    payload = {'totals': totals, 'thresholds': {
        'kana': args.max_kana, 'echo': args.max_echo,
        'variants': args.max_variants, 'cps': args.max_cps,
        'latin': args.max_latin, 'cyrillic': args.max_cyrillic,
        'cjk_space': args.max_cjk_space},
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
