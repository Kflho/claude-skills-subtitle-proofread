#!/usr/bin/env python3
"""参考字幕复用 · 配对：找出目标 cue 与参考字幕中**同一句话**的对。

三步：
  1. 锚点 —— 折叠相似度的**单调最长链**。总集篇沿原片顺序剪辑，对应行也单调，
     锚点夹出的参考区间就是候选窗口。
  2. 检索 —— 窗口内按折叠相似度取 top-K（阈值很低，靠复核兜底）。
  3. 复核 —— LLM 拿**源语言原句**判断「这条参考译文是不是这句的翻译」。这一步容忍
     ASR 误听与两位译者用词差异，是精确度的来源。通过后按相似度降序贪心去冲突
     （两侧单调且不重复）。

输出 <out-dir>/matches_<ep>.json（默认 out-dir = temp/reuse），交给 apply.py 改写。
复核结果按 (目标行, 参考行) 缓存在 <out-dir>/verify_<ep>.json，重跑只补新对；
只有拿到明确答复（yes/no）才入缓存，API 失败不写缓存。

Usage:
  python "<scripts>/reuse/align.py" -e SP01,SP02 \
    --source-dir temp/ja_raw --target-dir temp/zh_out \
    --reference-dir 字幕 --reference-glob '*.SC.ass' \
    --variants temp/reuse_variants.json --report temp/reuse/report.txt

  # 只对一集、先小批量试水
  python "<scripts>/reuse/align.py" -e SP01 --source-dir ... --target-dir ... \
    --reference-dir ... --topk 6
"""

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import lib._path  # noqa: F401,E402
from lib.config import LLM_API_KEY  # noqa: E402
from lib.llm import call_chat  # noqa: E402
from lib.video_scan import parse_episode_spec  # noqa: E402
from lib.whisper_utils import setup_windows_utf8  # noqa: E402

from reuse.common import (  # noqa: E402
    DEFAULT_STYLES, dice, group_hint, load_reference, load_target, load_variants,
    make_fold,
)

SYS_VERIFY = (
    '你是字幕校对。判断「参考译文」是否就是「源语言台词」这句话的翻译。\n'
    '· 源语言来自语音识别，**常有误听**（同一个人名会被听成不同的音），'
    '只差一两个音的词要按上下文理解。\n'
    '· 参考译文出自另一个人工翻译，用词可能与直译不同{hints}，'
    '也可能把两句合成一条、或把一句拆成两条。\n'
    '· 只要**语义确实对应同一句话**就答 yes；若说的是别的内容，或明显缺少/多出'
    '关键信息（例如源语言提到「温泉」而参考只说「真不愧是」），答 no。\n'
    '· 拿不准答 no。')


def ask_verify(pairs, model, hints):
    """pairs: [(源语言, 参考译文)] → [True/False/None]，None = 没拿到答复。"""
    body = [f'{k}. 源语言：{src}\n   参考译文：{ref}'
            for k, (src, ref) in enumerate(pairs, 1)]
    text = call_chat(
        [{'role': 'system', 'content': SYS_VERIFY.format(hints=hints)},
         {'role': 'user', 'content': '\n'.join(body)
          + '\n\n逐条判断，输出 JSON：{"1": "yes", "2": "no", ...}，不要解释。'}],
        model=model, temperature=0, max_tokens=1024, timeout=180)
    out = [None] * len(pairs)
    if not text:
        return out
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        return out
    try:
        d = json.loads(m.group(0).replace('，', ',').replace('：', ':'))
    except Exception:
        return out
    for k in range(1, len(pairs) + 1):
        v = str(d.get(str(k), '')).strip().lower()
        if v:
            out[k - 1] = v.startswith('y')
    return out


def anchor_chain(tgt, ref, thr, max_back):
    """折叠相似度的单调最长链 → [(目标行, 参考行)]。

    总集篇按原片顺序剪辑，所以对应关系单调；链给出的锚点用于夹出各行的候选窗口。
    """
    idx = {}
    for r in ref:
        for g in r['bg']:
            idx.setdefault(g, []).append(r['gi'])
    cands = []
    for c in tgt:
        seen = set()
        for g in c['bg']:
            seen.update(idx.get(g, ()))
        hits = sorted(((gi, dice(c['bg'], ref[gi]['bg'])) for gi in seen),
                      key=lambda x: -x[1])
        cands.append([(gi, s) for gi, s in hits if s >= thr][:6])
    dp, bk = {}, {}
    for i in range(len(tgt)):
        for gi, s in cands[i]:
            best, bj = s, None
            for j in range(i - 1, max(-1, i - max_back), -1):
                for gj, _ in cands[j]:
                    if gj < gi and (j, gj) in dp and dp[(j, gj)] + s > best:
                        best, bj = dp[(j, gj)] + s, (j, gj)
            dp[(i, gi)], bk[(i, gi)] = best, bj
    chain, k = [], (max(dp, key=dp.get) if dp else None)
    while k is not None:
        chain.append(k)
        k = bk[k]
    return list(reversed(chain))


def pair_up(tag, tgt, ref, args, fold):
    """→ {目标行: {gi, ...}}，命中的对。"""
    chain = anchor_chain(tgt, ref, args.anchor_thr, args.anchor_back)
    print(f'{tag}: 锚点 {len(chain)}/{len(tgt)}', flush=True)
    anchors = dict(chain)

    # 相邻锚点夹出的窗口；锚点自身给个小窗，落在区间外的兜底给前 120 条
    pts = [(-1, -1)] + list(chain) + [(len(tgt), len(ref))]
    wins = {}
    for (i1, g1), (i2, g2) in zip(pts, pts[1:]):
        lo = max(0, g1 - 3) if g1 >= 0 else 0
        hi = min(len(ref), g2 + 4)
        for i in range(i1 + 1, i2):
            wins[i] = (lo, hi)
    for i, gi in anchors.items():
        wins[i] = (max(0, gi - 4), min(len(ref), gi + 5))
    for i in range(len(tgt)):
        wins.setdefault(i, (0, min(len(ref), 120)))

    pairs = []
    for i, c in enumerate(tgt):
        lo, hi = wins[i]
        hits = sorted(((gi, dice(c['bg'], ref[gi]['bg'])) for gi in range(lo, hi)),
                      key=lambda x: -x[1])[:args.topk]
        pairs.extend((i, gi, s) for gi, s in hits if s >= args.sim_floor)
    for i, gi in anchors.items():          # 锚点也复核，不直接采信
        if all(p[0] != i for p in pairs):
            pairs.append((i, gi, dice(tgt[i]['bg'], ref[gi]['bg'])))
    print(f'   待复核对 {len(pairs)}', flush=True)

    cpath = os.path.join(args.out_dir, f'verify_{tag}.json')
    cache = json.load(open(cpath, encoding='utf-8')) if os.path.exists(cpath) else {}
    todo = [(f'{i}:{gi}', i, gi) for i, gi, _ in pairs if f'{i}:{gi}' not in cache]
    print(f'   新复核 {len(todo)}', flush=True)

    if todo:
        if not LLM_API_KEY:
            raise SystemExit('[reuse] LLM_API_KEY 未设置 —— 复核是配对的精确度来源，'
                             '不能跳过。设置 key 后重跑（已复核的结果有缓存，不会重复计费）')
        hints = group_hint(args.groups)
        batches = [todo[k:k + args.batch] for k in range(0, len(todo), args.batch)]

        def work(b):
            pl = [(tgt[i]['src'], ref[gi]['text'].replace('\n', ' ')) for _, i, gi in b]
            return b, ask_verify(pl, args.model, hints)

        answered = 0
        with ThreadPoolExecutor(args.workers) as ex:
            for b, res in zip(batches, ex.map(work, batches)):
                for (key, _, _), r in zip(b, res):
                    if r is not None:      # 失败不入缓存，下次重试
                        cache[key] = bool(r)
                        answered += 1
        json.dump(cache, open(cpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        if not answered:
            raise SystemExit(f'[reuse] {len(todo)} 对新复核全部失败（API key / 网络 / 模型名）。'
                             f'没有复核结果就无法配对 —— 修好后重跑')

    ok = [(i, gi, s) for i, gi, s in pairs if cache.get(f'{i}:{gi}')]
    print(f'   复核通过 {len(ok)}/{len(pairs)}', flush=True)

    # 去冲突：按相似度降序贪心，两侧单调且不重复
    ok.sort(key=lambda x: -x[2])
    final = {}
    for i, gi, _ in ok:
        if i in final or any(g == gi for g in final.values()):
            continue
        if any((j < i and g > gi) or (j > i and g < gi) for j, g in final.items()):
            continue
        final[i] = gi
    print(f'{tag}: 最终命中 {len(final)}/{len(tgt)}', flush=True)

    recs = {}
    for i, gi in sorted(final.items()):
        r = ref[gi]
        recs[str(i)] = {'gi': gi, 'ref_ep': r['ep'], 'ref_start': r['start'],
                        'ref_text': r['text'], 'tgt_start': tgt[i]['start'],
                        'src': tgt[i]['src'], 'zh': tgt[i]['zh'],
                        'sim': round(dice(tgt[i]['bg'], r['bg']), 3)}
    return recs


def main():
    setup_windows_utf8()
    ap = argparse.ArgumentParser(description='参考字幕复用 · 配对（同一句话的候选对）')
    ap.add_argument('-e', '--episodes', required=True,
                    help='目标集号，逗号/区间：SP01,SP02 / EP001-EP003')
    ap.add_argument('--source-dir', required=True, help='源语言转录目录（复核要看源语言原句）')
    ap.add_argument('--target-dir', required=True, help='我们的译文目录')
    ap.add_argument('--reference-dir', required=True, help='参考字幕目录')
    ap.add_argument('--reference-glob', default=None,
                    help="参考文件通配（如 '*.SC.ass'）；不给则收目录下全部 .srt/.ass")
    ap.add_argument('--styles', default=DEFAULT_STYLES,
                    help=f"参考字幕的 ASS 对白样式，逗号分隔（默认 {DEFAULT_STYLES}）；'' 关闭过滤")
    ap.add_argument('--variants', default=None, help='写法归一分组 JSON（见 references）')
    ap.add_argument('--out-dir', default='temp/reuse', help='输出目录（默认 temp/reuse）')
    ap.add_argument('--report', default=None, help='另存一份人读报告')
    ap.add_argument('--model', default=None, help='复核用模型（默认 LLM_MODEL）')
    ap.add_argument('--topk', type=int, default=10, help='窗口内每行取多少候选（默认 10）')
    ap.add_argument('--sim-floor', type=float, default=0.12,
                    help='候选相似度下限（默认 0.12；靠复核兜底，低一点不漏）')
    ap.add_argument('--anchor-thr', type=float, default=0.60, help='锚点相似度阈值')
    ap.add_argument('--anchor-back', type=int, default=80, help='锚点链的回溯跨度')
    ap.add_argument('--batch', type=int, default=10, help='每次复核请求几对（默认 10）')
    ap.add_argument('--workers', type=int, default=8, help='复核并发数（默认 8）')
    args = ap.parse_args()

    tags = parse_episode_spec(args.episodes)
    if not tags:
        raise SystemExit(f'[reuse] 集号无法解析：{args.episodes}')
    os.makedirs(args.out_dir, exist_ok=True)
    args.groups, _ = load_variants(args.variants)
    fold = make_fold(args.groups)

    ref = load_reference(args.reference_dir, args.reference_glob, args.styles, fold)
    print(f'参考 cue {len(ref)}（{args.reference_dir}）', flush=True)

    lines = []
    for tag in tags:
        tgt = load_target(args.source_dir, args.target_dir, tag, fold)
        recs = pair_up(tag, tgt, ref, args, fold)
        json.dump({'n_target': len(tgt), 'n_reference': len(ref), 'matches': recs},
                  open(os.path.join(args.out_dir, f'matches_{tag}.json'), 'w',
                       encoding='utf-8'), ensure_ascii=False, indent=1)
        diff = [r for r in recs.values() if r['zh'].strip() != r['ref_text'].strip()]
        lines.append(f'══ {tag}: 命中 {len(recs)}/{len(tgt)}，文本有差异 {len(diff)}')
        for k, r in recs.items():
            mark = '改' if r['zh'].strip() != r['ref_text'].strip() else '＝'
            lines.append(f"#{int(k) + 1:3d} [{mark}] sim={r['sim']:.2f}\n"
                         f"    我: {r['zh']}\n"
                         f"    参考: {r['ref_ep']} {r['ref_start'][:8]}  {r['ref_text']}")
    if args.report:
        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        open(args.report, 'w', encoding='utf-8').write('\n'.join(lines))
        print(f'→ {args.report}')
    print(f"→ {os.path.join(args.out_dir, 'matches_<集号>.json')}；下一步 reuse/apply.py")


if __name__ == '__main__':
    main()
