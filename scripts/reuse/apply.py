#!/usr/bin/env python3
"""参考字幕复用 · 改写：把参考译文覆盖到我们的机译上。

输入 align.py 的 matches_<ep>.json。**先 dry-run 看报告，再 --apply**：
参考字幕未必是同一部作品（不是总集篇就没有可复用素材），判据宁可漏配不可错配。

采纳判据：
  R1 目标文本 < --min-len 字 → 丢（短句极易撞车，换了也没多少信息量）
  R2 两文本相同 / 只差标点 → 不改（是噪声，不是修订）
  R3 参考文本 < --ratio × 目标文本 → 丢（是片段而非同句，换了会丢信息）
  R4 相似度 < --min-sim → 丢（判断「没有可复用素材」就靠它）
  R5 never_swap 里的两种写法不得互换（昵称 vs 形态名、简称 vs 全称）
  R6 参考行唯一：同一条参考 cue 只被采纳一次，冲突时按相似度降序取先者
  · 折叠后两份文本**完全相同**的（整句只差人名写法）不受 R1/R3 约束 ——
    那正是要按参考定译改写的情形（「我是瓦尔古蕾」→「我是瓦尔Q蕾」）
采纳后并进同集相邻未占用的参考行（参考把一句拆成两三条时）。

--apply 写盘前把目标备份为 <ep>.srt.bak（已存在则不覆盖，保住最初那份）。

Usage:
  python "<scripts>/reuse/apply.py" -e SP01 \
    --source-dir temp/ja_raw --target-dir temp/zh_out \
    --reference-dir 字幕 --reference-glob '*.SC.ass' \
    --variants temp/reuse_variants.json            # dry-run
  python "<scripts>/reuse/apply.py" -e SP01 ... --apply
"""

import argparse
import json
import os
import shutil

import lib._path  # noqa: F401,E402
from lib.subtitle_io import read_subtitles, write_subtitles  # noqa: E402
from lib.video_scan import parse_episode_spec  # noqa: E402
from lib.whisper_utils import setup_windows_utf8  # noqa: E402

from reuse.common import (  # noqa: E402
    DEFAULT_STYLES, bg, clean, dice, load_reference, load_target, load_variants,
    make_fold, norm_only, resolve_tag_file,
)


def judge(i, tgt, ref, gi, min_sim, min_len, ratio, never_swap):
    """→ (采纳?, 理由, 参考起行, 参考止行)。"""
    raw_tgt, raw_ref = clean(tgt[i]['zh']), clean(ref[gi]['text'])
    if raw_tgt == raw_ref:
        return False, 'R2完全相同', gi, gi
    if norm_only(raw_tgt) == norm_only(raw_ref):
        return False, 'R2仅标点差', gi, gi
    # 折叠后相同 = 整句只差人名写法，不受长度判据约束
    name_only = tgt[i]['f'] == ref[gi]['f']
    if not name_only:
        if len(tgt[i]['f']) < min_len:
            return False, 'R1短句', gi, gi
        if len(ref[gi]['f']) < ratio * len(tgt[i]['f']):
            return False, f"R3片段({len(ref[gi]['f'])}/{len(tgt[i]['f'])})", gi, gi
    for a, b in never_swap:
        if (a in raw_tgt and b in raw_ref and a not in raw_ref) or \
           (b in raw_tgt and a in raw_ref and b not in raw_tgt):
            return False, f'R5写法不符({a}/{b})', gi, gi
    sim = dice(tgt[i]['bg'], ref[gi]['bg'])
    if sim < min_sim:
        return False, f'R4相似{sim:.2f}<{min_sim}', gi, gi
    return True, ('形名' if name_only else f'sim={sim:.2f}'), gi, gi


def extend(i, a, b, tgt, ref, used, adj_gap, max_ext, fold):
    """参考把一句拆成连续多条时，向后并进未占用的续行。"""
    f_tgt = len(tgt[i]['f'])
    cur = ' '.join(ref[g]['text'].replace('\n', '') for g in range(a, b + 1))
    while b + 1 < len(ref) and (b + 1) not in used:
        nxt = ref[b + 1]
        if nxt['ep'] != ref[b]['ep'] or nxt['start_s'] - ref[b]['start_s'] > adj_gap:
            break
        cand = cur + nxt['text'].replace('\n', '')
        if len(fold(cand)) > max_ext * f_tgt:
            break
        if dice(tgt[i]['bg'], bg(fold(cand))) < dice(tgt[i]['bg'], bg(fold(cur))):
            break
        cur, b = cand, b + 1
    return a, b


def run(tag, args, ref, fold):
    path = resolve_tag_file(args.target_dir, tag)
    if not path:
        raise SystemExit(f'[reuse] {args.target_dir} 里找不到 {tag} 的字幕')
    tgt = load_target(args.source_dir, args.target_dir, tag, fold)
    mpath = os.path.join(args.out_dir, f'matches_{tag}.json')
    if not os.path.exists(mpath):
        raise SystemExit(f'[reuse] 缺 {mpath} —— 先跑 reuse/align.py')
    matches = json.load(open(mpath, encoding='utf-8'))['matches']
    lines = [f'══ {tag}: 候选 {len(matches)} / {len(tgt)} cue'
             f'   (min-sim={args.min_sim} ratio={args.ratio} min-len={args.min_len})']

    acc, rej = [], []
    for k, m in sorted(matches.items(), key=lambda x: int(x[0])):
        i = int(k)
        ok, why, a, b = judge(i, tgt, ref, m['gi'], args.min_sim, args.min_len,
                              args.ratio, args.never_swap)
        (acc if ok else rej).append((i, a, b, why))

    # 续行合并 + 冲突再消解（参考行只能被用一次）
    used, final = set(), {}
    for i, a, b, why in acc:
        if any(g in used for g in range(a, b + 1)):
            rej.append((i, a, b, 'R6与已采纳冲突'))
            continue
        a, b = extend(i, a, b, tgt, ref, used, args.adj_gap, args.max_ext, fold)
        if any(g in used for g in range(a, b + 1)):
            rej.append((i, a, b, 'R6与已采纳冲突'))
            continue
        used.update(range(a, b + 1))
        final[i] = (a, b, why)

    lines.append(f'── 采纳 {len(final)}  丢弃 {len(rej)}')
    lines.append('\n【采纳】')
    for i in sorted(final):
        a, b = final[i][0], final[i][1]
        txt = '\n'.join(ref[g]['text'] for g in range(a, b + 1))
        lines.append(f"#{i + 1:3d} {tgt[i]['start'][:8]} [{final[i][2]}] "
                     f"{ref[a]['ep']} {ref[a]['start'][:8]}"
                     f"{'..' + ref[b]['start'][:8] if b > a else ''}\n"
                     f"    我: {tgt[i]['zh']}\n"
                     f"    参考: {txt}")
    lines.append('\n【丢弃】')
    for i, a, b, why in sorted(rej):
        lines.append(f"#{i + 1:3d} {tgt[i]['start'][:8]} {why}\n"
                     f"    我: {tgt[i]['zh']}\n"
                     f"    参考: {ref[a]['text']}")
    rpath = os.path.join(args.out_dir, f'apply_{tag}.txt')
    open(rpath, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'{tag}: 采纳 {len(final)}  丢弃 {len(rej)}  → {rpath}')

    if not (args.apply and final):
        return
    bak = path + '.bak'
    if not os.path.exists(bak):
        shutil.copyfile(path, bak)
    as_newline = ('\n' if path.lower().endswith('.srt') else chr(92) + 'N')
    cues = read_subtitles(path, mark_garbled=False)
    n = 0
    for i, (a, b, _) in final.items():
        txt = '\n'.join(ref[g]['text'] for g in range(a, b + 1))
        cues[i]['text'] = txt.replace('\n', as_newline)
        n += 1
    write_subtitles(path, cues)
    print(f'   已改写 {n} 条 → {path}（备份 {bak}）')


def main():
    setup_windows_utf8()
    ap = argparse.ArgumentParser(description='参考字幕复用 · 改写（参考译文覆盖机译）')
    ap.add_argument('-e', '--episodes', required=True, help='目标集号：SP01 / SP01,SP02')
    ap.add_argument('--source-dir', required=True, help='源语言转录目录')
    ap.add_argument('--target-dir', required=True, help='我们的译文目录（将被改写）')
    ap.add_argument('--reference-dir', required=True, help='参考字幕目录')
    ap.add_argument('--reference-glob', default=None, help="参考文件通配（如 '*.SC.ass'）")
    ap.add_argument('--styles', default=DEFAULT_STYLES,
                    help=f"参考字幕的 ASS 对白样式（默认 {DEFAULT_STYLES}）；'' 关闭过滤")
    ap.add_argument('--variants', default=None, help='写法归一分组 JSON（见 references）')
    ap.add_argument('--out-dir', default='temp/reuse', help='align.py 的输出目录')
    ap.add_argument('--min-sim', type=float, default=0.12,
                    help='R4 相似度下限（默认 0.12 = 确认有复用素材；拿不准就提到 0.45）')
    ap.add_argument('--ratio', type=float, default=0.58, help='R3 参考/目标长度比下限')
    ap.add_argument('--min-len', type=int, default=4, help='R1 目标文本最短字数')
    ap.add_argument('--max-ext', type=float, default=1.35, help='续行合并后总长上限倍数')
    ap.add_argument('--adj-gap', type=float, default=4.0, help='续行合并的起始间隔上限（秒）')
    ap.add_argument('--apply', action='store_true', help='写盘（默认 dry-run）')
    args = ap.parse_args()

    tags = parse_episode_spec(args.episodes)
    if not tags:
        raise SystemExit(f'[reuse] 集号无法解析：{args.episodes}')
    os.makedirs(args.out_dir, exist_ok=True)
    groups, args.never_swap = load_variants(args.variants)
    fold = make_fold(groups)

    ref = load_reference(args.reference_dir, args.reference_glob, args.styles, fold)
    print(f'参考 cue {len(ref)}（{args.reference_dir}）')
    for tag in tags:
        run(tag, args, ref, fold)
    if not args.apply:
        print('（dry-run）确认报告后加 --apply 写盘')


if __name__ == '__main__':
    main()
