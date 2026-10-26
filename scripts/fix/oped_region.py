#!/usr/bin/env python3
"""按「每集 OP/ED 窗口」把该区域的歌词 cue 换成定本歌词。

背景：ASR 对唱词基本是乱写（`超大な鼓動へ` 这类），ForcedAligner 也不给唱段
时间戳。可靠做法是先用音频指纹定位每集的 OP/ED 窗口
（`audio/oped_boundaries.py`，画面复核见 `--frames-dir`），再按窗口把该段
的 cue 换成人工定稿的歌词；时间沿用原 cue 的分布（唱词 cue 的位置本身就是
「这一句在唱」的证据），不重打轴、不碰窗口外的对白。

用法：
  python scripts/fix/oped_region.py <字幕目录> \
    --windows data/temp/oped_windows.json \
    --op-lines data/temp/op_zh.txt --ed-lines data/temp/ed_zh.txt \
    --out-dir data/temp/oped_out          # 默认只写副本

  ... --dry-run        # 只报告
  ... --in-place       # 原地写回（每文件先 .bak）

`--op-lines/--ed-lines` 是一行一句的定本歌词（UTF-8）。窗口里原本有几条 cue、
定本有几行，都行：行数少于 cue 数时按组合并，行数多于 cue 数时在首末 cue 之间
均匀铺开。窗口里没有任何 cue 时跳过（不凭空造字幕）。
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402
from lib.subtitle_io import format_tc, read_subtitles, write_subtitles  # noqa: E402

MIN_DUR = 0.40      # 一条歌词最短显示时长（秒）
GAP = 0.05          # 相邻 cue 之间的缝


def load_lines(path: str) -> list[str]:
    if not path:
        return []
    with io.open(path, encoding='utf-8-sig') as f:
        return [ln.strip() for ln in f if ln.strip()]


def ep_key(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem


def region_lines(windows: dict, ep: str, tag: str) -> dict | None:
    rec = windows.get(ep) or windows.get(ep.upper())
    if not rec:
        return None
    return rec.get(tag)


def spread_times(lo: float, hi: float, weights: list[float]) -> list[tuple[float, float]]:
    """把定本歌词铺到 [lo, hi] 上，按每行的权重（默认字符数）分配时长。

    唱段里 ASR 的 cue 边界是「哪一句在唱」的证据，但块长很不均匀
    （合并成 10 秒一条很常见），直接按 cue 边界铺会把某一句拉成 16 秒。
    按行字数分配更稳：一首歌每句的演唱时长大致跟音节数成正比，中文译句的
    字数是最容易拿到的近似。
    """
    n = len(weights)
    if n == 0 or hi <= lo:
        return []
    total = float(sum(weights)) or float(n)
    out = []
    t = lo
    for w in weights:
        d = (hi - lo) * (float(w) / total)
        out.append((t, t + d))
        t += d
    out[-1] = (out[-1][0], hi)
    return out


def rebuild_region(cues: list[dict], win: dict, lines: list[str],
                   lang: str = 'zh') -> tuple[list[dict], int, int]:
    """替换一个区域，返回 (新 cue 列表, 删掉的条数, 插入的行数)。"""
    lo, hi = float(win['start']), float(win['end'])
    inside = [i for i, c in enumerate(cues)
              if float(c.get('start_s', 0)) < hi and float(c.get('end_s', 0)) > lo]
    if not inside or not lines:
        return cues, 0, 0

    anchors = [float(cues[i]['start_s']) for i in inside]
    anchors.append(min(hi, float(cues[inside[-1]]['end_s'])))
    lo_a, hi_a = min(anchors), max(anchors)
    weights = [max(1, len(line)) for line in lines]
    times = spread_times(lo_a, hi_a, weights)
    if not times:
        return cues, 0, 0

    new_cues = []
    for k, text in enumerate(lines):
        start, end = times[k]
        if k + 1 < len(times):
            end = min(end, times[k + 1][0] - GAP)
        end = max(end, start + MIN_DUR)
        end = min(end, hi)
        new_cues.append({
            'start': format_tc(start), 'end': format_tc(end),
            'start_s': round(start, 3), 'end_s': round(end, 3),
            'text': text, 'lang': lang,
        })

    kept = [c for i, c in enumerate(cues) if i not in set(inside)]
    merged = kept + new_cues
    merged.sort(key=lambda c: (float(c.get('start_s', 0)),
                               float(c.get('end_s', 0))))
    return merged, len(inside), len(new_cues)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='按 OP/ED 窗口用定本歌词替换该段 cue')
    ap.add_argument('target_dir', help='字幕目录（SRT/ASS）')
    ap.add_argument('--windows', required=True,
                    help='oped_boundaries.py 输出的 JSON')
    ap.add_argument('--op-lines', default='', help='片头定本歌词（一行一句）')
    ap.add_argument('--ed-lines', default='', help='片尾定本歌词（一行一句）')
    ap.add_argument('--lang', default='zh', help='目标语言标签（默认 zh）')
    ap.add_argument('--out-dir', default='', help='副本输出目录；不给且不 --in-place 就只报告')
    ap.add_argument('--in-place', action='store_true', help='原地写回（先 .bak）')
    ap.add_argument('--episodes', '-e', default='', help='EP001-EP005 / 1-5')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    with io.open(args.windows, encoding='utf-8') as f:
        windows = json.load(f)

    op_lines = load_lines(args.op_lines)
    ed_lines = load_lines(args.ed_lines)
    if not op_lines and not ed_lines:
        print('error: need --op-lines and/or --ed-lines', file=sys.stderr)
        return 2

    only = set()
    if args.episodes:
        for part in args.episodes.replace('EP', '').split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                a, b = part.split('-', 1)
                only.update(range(int(a), int(b) + 1))
            else:
                only.add(int(part))

    files = sorted(glob.glob(os.path.join(args.target_dir, '*.srt'))
                   + glob.glob(os.path.join(args.target_dir, '*.ass')))
    if not files:
        print(f'error: no subtitle files in {args.target_dir}', file=sys.stderr)
        return 1

    changed = skipped = 0
    for path in files:
        ep = ep_key(path)
        try:
            num = int(''.join(ch for ch in ep if ch.isdigit()) or 0)
        except ValueError:
            num = 0
        if only and num not in only:
            continue
        cues = list(read_subtitles(path, mark_garbled=False))
        if not cues:
            continue
        before = len(cues)
        detail = []
        for tag, lines in (('op', op_lines), ('ed', ed_lines)):
            if not lines:
                continue
            win = region_lines(windows, ep, tag)
            if not win:
                continue
            cues, removed, inserted = rebuild_region(cues, win, lines, args.lang)
            if removed:
                detail.append(f'{tag}:{removed}→{inserted}')
        if not detail:
            skipped += 1
            print(f'[oped-region] {ep}: no region cues, skip', file=sys.stderr)
            continue
        print(f'[oped-region] {ep}: {", ".join(detail)} (cues {before}→{len(cues)})',
              file=sys.stderr)
        changed += 1
        if args.dry_run:
            continue
        if args.in_place:
            bak = path + '.bak'
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            write_subtitles(path, cues, template_path=path)
        elif args.out_dir:
            os.makedirs(args.out_dir, exist_ok=True)
            out = os.path.join(args.out_dir, os.path.basename(path))
            write_subtitles(out, cues, template_path=path)

    print(f'[oped-region] {changed} file(s) changed, {skipped} skipped'
          + (' (dry-run)' if args.dry_run else ''), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
