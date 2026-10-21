#!/usr/bin/env python3
"""重译代价评估：先量旧稿有多烂，再算重译要花多少，给结论。

用法：
    python scripts/eval/retranslate_decision.py \
        --zh-dir <旧译文目录> --source-dir <源字幕目录> \
        --glossary <术语表> --report temp/decision.json

    # 只算成本，不判质量（新项目）
    python scripts/eval/retranslate_decision.py --zh-dir <目录> --cost-only

为什么需要它：`--repair` 只修 QA 标出来的条目，`translate_srt.py` 一跑就是
整集重翻。中间那条路——「这批稿子是修还是重翻」——一直靠人拍脑袋。
本工具把它变成可复算的三个数：缺陷率、修 vs 翻的调用量、墙钟时间。

判据（阈值见 DEFAULT_RULES，可 JSON 覆盖）：
    · 西里尔/假名残留 > 0        → 必重译（译名或整条没翻完）
    · echo（原文回显）> 0.5%     → 必重译
    · 标点缺陷（空格断句/无标点）> 25% → 重译
    · 与源条数漂移 > 5%          → 重译（丢条/合并，修不回来）
    · 其余缺陷 5%~25%            → 定向修（只重写问题条）
    · < 5%                       → 保留，按 QA 清单人工过
"""

import argparse
import glob
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402

from eval.text_gate import CYRILLIC, spaced_as_punctuation  # noqa: E402
from lib.subtitle_io import read_subtitles  # noqa: E402
from lib.translation.qa import count_kana, is_untranslated, strip_tags  # noqa: E402

PUNCT = re.compile(r'[，。！？…、,.!?~～；：]')
# 成本估算用的经验值；都是「每 cue」量级，不是精确计费，用来比较修 vs 翻。
DEFAULT_COST = {
    'batch_size': 20,        # translate_srt 默认批大小
    'parallel': 16,          # ThreadPoolExecutor 默认并发
    'latency_s': 14.0,       # 单批往返（含重试后取均值）
    'retry_factor': 1.35,    # 拆批/重试带来的额外调用
    'src_tokens_per_cue': 18,
    'zh_tokens_per_cue': 22,
    'price_in_per_mtok': 0.0,   # 填了才算钱，单位：元/百万 token
    'price_out_per_mtok': 0.0,
    'repair_batch': 10,      # 定向修：一次请求塞几条
}

DEFAULT_RULES = {
    'cyrillic_ratio': 0.0001,
    'echo_ratio': 0.005,
    'punct_ratio': 0.25,
    'drift_ratio': 0.05,
    'patch_upper': 0.25,
    'patch_lower': 0.05,
}


def _text(cue):
    """可见正文（去掉 ASS/HTML 标签）——标签里的 `fad` 之类会被当成英文残留。"""
    return strip_tags(str(cue.get('text') or '')).strip()


def score_episode(zh_cues, src_cues=None):
    """单集质量画像。src_cues 缺失时跳过 echo / 条数漂移。"""
    total = len(zh_cues)
    cyrillic = kana = echo = cjk_space = nopunct = overlong = 0
    for i, cue in enumerate(zh_cues):
        text = _text(cue)
        if not text:
            continue
        if CYRILLIC.search(text):
            cyrillic += 1
        if count_kana(text):
            kana += 1
        if spaced_as_punctuation(text):
            cjk_space += 1
        if len(text) > 8 and not PUNCT.search(text):
            nopunct += 1
        if len(text) > 22:
            overlong += 1
        if src_cues is not None and i < len(src_cues):
            if is_untranslated(_text(src_cues[i]), text):
                echo += 1
    src_total = len(src_cues) if src_cues is not None else None
    drift = (abs(total - src_total) / src_total) if src_total else 0.0
    punct_defect = cjk_space + nopunct
    return {
        'cues': total,
        'src_cues': src_total,
        'cyrillic': cyrillic,
        'kana': kana,
        'echo': echo,
        'cjk_space': cjk_space,
        'nopunct': nopunct,
        'overlong': overlong,
        'punct_defect': punct_defect,
        'drift': round(drift, 4),
    }


def aggregate(scores):
    """把逐集画像汇总成比率。分母是总 cue 数，漂移取加权平均。"""
    cues = sum(s['cues'] for s in scores) or 1
    src_cues = sum(s['src_cues'] or 0 for s in scores)
    drift = (abs(cues - src_cues) / src_cues) if src_cues else 0.0
    totals = {
        'cues': cues,
        'src_cues': src_cues or None,
        'cyrillic': sum(s['cyrillic'] for s in scores),
        'kana': sum(s['kana'] for s in scores),
        'echo': sum(s['echo'] for s in scores),
        'cjk_space': sum(s['cjk_space'] for s in scores),
        'nopunct': sum(s['nopunct'] for s in scores),
        'overlong': sum(s['overlong'] for s in scores),
        'punct_defect': sum(s['punct_defect'] for s in scores),
        'drift': round(drift, 4),
    }
    totals['ratios'] = {
        'cyrillic': totals['cyrillic'] / cues,
        'kana': totals['kana'] / cues,
        'echo': totals['echo'] / cues,
        'punct': totals['punct_defect'] / cues,
        'overlong': totals['overlong'] / cues,
    }
    totals['ratios'] = {k: round(v, 4) for k, v in totals['ratios'].items()}
    return totals


def estimate_cost(cues, cost=None, defect_cues=0):
    """重译 vs 定向修 的调用量、token、墙钟时间估算。"""
    c = dict(DEFAULT_COST)
    c.update(cost or {})
    batches = max(1, math.ceil(cues / c['batch_size']))
    calls_retranslate = math.ceil(batches * c['retry_factor'])
    retranslate = {
        'calls': calls_retranslate,
        'src_tokens': int(cues * c['src_tokens_per_cue'] * c['retry_factor']),
        'zh_tokens': int(cues * c['zh_tokens_per_cue'] * c['retry_factor']),
        'wall_minutes': round(
            calls_retranslate / max(1, c['parallel']) * c['latency_s'] / 60, 1),
    }
    repair_calls = math.ceil(defect_cues / c['repair_batch']) if defect_cues else 0
    repair = {
        'cues': defect_cues,
        'calls': repair_calls,
        'src_tokens': int(defect_cues * c['src_tokens_per_cue'] * 1.2),
        'zh_tokens': int(defect_cues * c['zh_tokens_per_cue'] * 1.2),
        'wall_minutes': round(
            repair_calls / max(1, c['parallel']) * c['latency_s'] / 60, 1),
    }
    for plan in (retranslate, repair):
        plan['price_cny'] = round(
            (plan['src_tokens'] * c['price_in_per_mtok']
             + plan['zh_tokens'] * c['price_out_per_mtok']) / 1e6, 2)
    return {'retranslate': retranslate, 'repair': repair,
            'ratio': round(repair['calls'] / retranslate['calls'], 3)
            if retranslate['calls'] else 0.0}


def decide(totals, rules=None):
    """按缺陷结构给 verdict：retranslate / patch / keep。"""
    r = dict(DEFAULT_RULES)
    r.update(rules or {})
    ratios = totals['ratios']
    reasons = []
    fatal = False
    if ratios['cyrillic'] > r['cyrillic_ratio']:
        fatal = True
        reasons.append(f"cyrillic {ratios['cyrillic']:.2%}")
    if ratios['kana'] > r['cyrillic_ratio']:
        fatal = True
        reasons.append(f"kana {ratios['kana']:.2%}")
    if ratios['echo'] > r['echo_ratio']:
        fatal = True
        reasons.append(f"echo {ratios['echo']:.2%}")
    if totals['drift'] > r['drift_ratio']:
        fatal = True
        reasons.append(f"cue drift {totals['drift']:.2%}")
    if ratios['punct'] > r['punct_ratio']:
        fatal = True
        reasons.append(f"punct defect {ratios['punct']:.2%}")
    if fatal:
        return 'retranslate', reasons
    worst = max(ratios['punct'], ratios['overlong'])
    if worst > r['patch_upper']:
        return 'retranslate', [f'defect {worst:.2%} > {r["patch_upper"]:.0%}']
    if worst > r['patch_lower']:
        return 'patch', [f'defect {worst:.2%} in patch band']
    return 'keep', [f'defect {worst:.2%} < {r["patch_lower"]:.0%}']


def _ep_key(path):
    """配对键：优先文件名里最后一个数字组（001 / Ep001 / EP001 都归一到 1）。

    字幕产物的命名在源/译之间几乎从来不一致（`Mahou Tsukai Sally 001.ass`
    vs `..._Ep001_Simu.ass`），按整名配对必然配不上。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    nums = re.findall(r'\d+', stem)
    if nums:
        return 'ep' + str(int(nums[-1]))
    return re.sub(r'[^0-9a-zA-Z]+', '', stem).lower()


def _pairs(zh_dir, src_dir):
    """按集号配对，源目录可缺省（返回 [(zh_path, src_path|None)]）。"""
    zh = sorted(glob.glob(os.path.join(zh_dir, '*.srt'))
                + glob.glob(os.path.join(zh_dir, '*.ass')))
    src_index = {}
    if src_dir:
        for path in sorted(glob.glob(os.path.join(src_dir, '*.srt'))
                           + glob.glob(os.path.join(src_dir, '*.ass'))):
            src_index.setdefault(_ep_key(path), path)
    return [(path, src_index.get(_ep_key(path))) for path in zh]


def run(zh_dir, src_dir='', rules=None, cost=None, cost_only=False):
    scores = []
    files = []
    for zh_path, src_path in _pairs(zh_dir, src_dir):
        zh_cues = read_subtitles(zh_path, mark_garbled=False)
        src_cues = (read_subtitles(src_path, mark_garbled=False)
                    if src_path else None)
        score = score_episode(zh_cues, src_cues)
        score['file'] = os.path.basename(zh_path)
        scores.append(score)
        files.append(score)
    totals = aggregate(scores)
    defect_cues = totals['punct_defect'] + totals['overlong']
    money = estimate_cost(totals['cues'], cost=cost, defect_cues=defect_cues)
    if cost_only:
        verdict, reasons = 'n/a', ['cost-only']
    else:
        verdict, reasons = decide(totals, rules=rules)
    return {'zh_dir': zh_dir, 'source_dir': src_dir, 'episodes': len(files),
            'totals': totals, 'cost': money, 'verdict': verdict,
            'reasons': reasons, 'files': files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--zh-dir', required=True)
    parser.add_argument('--source-dir', default='')
    parser.add_argument('--rules', default='', help='JSON 覆盖阈值')
    parser.add_argument('--cost', default='', help='JSON 覆盖成本参数')
    parser.add_argument('--cost-only', action='store_true')
    parser.add_argument('--report', default='')
    parser.add_argument('--price-in', type=float, default=None,
                        help='输入价格，元/百万 token')
    parser.add_argument('--price-out', type=float, default=None)
    args = parser.parse_args(argv)

    rules = json.load(open(args.rules, encoding='utf-8')) if args.rules else None
    cost = json.load(open(args.cost, encoding='utf-8')) if args.cost else None
    if args.price_in is not None or args.price_out is not None:
        cost = dict(cost or {})
        if args.price_in is not None:
            cost['price_in_per_mtok'] = args.price_in
        if args.price_out is not None:
            cost['price_out_per_mtok'] = args.price_out

    result = run(args.zh_dir, args.source_dir, rules=rules, cost=cost,
                 cost_only=args.cost_only)
    t = result['totals']
    print(f"[decision] {result['episodes']} episode(s), {t['cues']} cues "
          f"-> {result['verdict'].upper()}  ({', '.join(result['reasons'])})",
          file=sys.stderr)
    print(f"  ratios: cyrillic {t['ratios']['cyrillic']:.2%}, "
          f"echo {t['ratios']['echo']:.2%}, punct {t['ratios']['punct']:.2%}, "
          f"overlong {t['ratios']['overlong']:.2%}, drift {t['drift']:.2%}",
          file=sys.stderr)
    re_, re_pair = result['cost']['retranslate'], result['cost']['repair']
    print(f"  retranslate: {re_['calls']} calls, ~{re_['wall_minutes']} min, "
          f"{re_['price_cny']} CNY", file=sys.stderr)
    print(f"  repair     : {re_pair['calls']} calls, "
          f"~{re_pair['wall_minutes']} min, {re_pair['price_cny']} CNY "
          f"({re_pair['cues']} cues)", file=sys.stderr)
    if args.report:
        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        with open(args.report, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
    print(json.dumps({'verdict': result['verdict'],
                      'reasons': result['reasons'],
                      'totals': t,
                      'cost': result['cost']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
