#!/usr/bin/env python3
"""项目词典生成器 — 从 unified_scanner 输出中提取高频罗马字词，生成项目级替换词典。

用法:
  python build_project_dict.py --findings findings.json --output project_dict.json

输出 project_dict.json:
  {
    "auto": {                    # 内置词典命中，自动替换
      "atom": {"replacement": "アトム", "count": 45, "episodes": ["EP001","EP003"]},
      ...
    },
    "pending": {                 # 高频但未命中，待人工填写 replacement 字段
      "conoha": {"count": 5, "episodes": ["EP019"], "examples": ["...conoha"], "replacement": ""},
      ...
    },
    "noise": {                   # 疑似噪声（默认跳过），可手动确认后移入 pending
      "whni": {"count": 2, "episodes": ["EP002"], "reason": "无元音"},
      ...
    }
  }

用户填写 pending 中的 replacement 后，romaji_fixer.py --project-dict 会读取并合并。
noise 段默认不参与修复，除非手动移入 auto 或 pending。
"""

import argparse
import json
import re
import sys
import os
from collections import defaultdict

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from whisper_utils import setup_windows_utf8, extract_ep_number
from romaji_fixer import ROMAJI_TO_KANA, ROMAJI_CORRECTIONS, ENGLISH_OK, AI_NOISE

MIN_FREQ = 2        # 至少出现 2 次才纳入
MIN_WORD_LEN = 2     # 最短单词长度（排除单字符）

# ── 噪声检测规则 ──────────────────────────────────────────────

# 无元音字母（英语 a/e/i/o/u）
NO_VOWEL_RE = re.compile(r'^[^aeiou]+$')

# 已知噪声模式（辅音簇、Whisper 幻觉碎片）
KNOWN_NOISE_PATTERNS = {
    'wh', 'th', 'dj', 'pr', 'jr', 'dr', 'br', 'cr', 'fr',
    'gr', 'tr', 'wr', 'str', 'spr', 'scr', 'nd', 'ng', 'nk',
}

# 已知英文单词 — 在日语字幕中出现大概率是幻觉
ENGLISH_HALLUCINATION = {
    'car', 'box', 'moon', 'yahoo', 'add', 'the', 'by',
    'clover', 'group', 'whoop', 'hello', 'good', 'stop', 'go',
}

# 非英语但明显是噪声的模式（Whisper 幻觉碎片）
NONSENSE_NOISE = {
    'jej', 'whni', 'bejean', 'noneme', 'goonie', 'gootee',
    'memememe', 'meme',
}


def is_likely_noise(word: str) -> tuple[bool, str]:
    """判断一个词是否大概率是 AI 转录噪声。

    启发式规则（按优先级）：
      1. 无元音字母（a/e/i/o/u） → 噪声
      2. 已知噪声模式 → 噪声
      3. 已知英文幻觉词（car, box, moon 等） → 噪声
      4. 重复音节（meme, memememe, papapa） → 噪声
      5. 辅音占比 ≥75% → 噪声

    Returns:
        (is_noise: bool, reason: str)
    """
    if len(word) < 2:
        return True, '单字符'

    # 规则 1: 无元音
    if NO_VOWEL_RE.match(word):
        return True, '无元音（纯辅音）'

    # 规则 2: 已知噪声/幻觉模式
    if word in KNOWN_NOISE_PATTERNS:
        return True, f'已知噪声模式: {word}'
    if word in ENGLISH_HALLUCINATION:
        return True, f'英文幻觉词: {word}'
    if word in NONSENSE_NOISE:
        return True, f'无意义噪声: {word}'

    # 规则 4: 重复音节
    # 偶数长度：检查 2-char 音节重复（meme, papa, memememe）
    if len(word) >= 4 and len(word) % 2 == 0:
        syl2 = word[:2]
        repeats = len(word) // 2
        if word == syl2 * repeats:
            return True, f'重复音节: {syl2}×{repeats}'
    # 6+ 字符：检查 3-char 音节重复
    if len(word) >= 6 and len(word) % 3 == 0:
        syl3 = word[:3]
        if word == syl3 * (len(word) // 3):
            return True, f'重复音节: {syl3}×{len(word)//3}'

    # 规则 5: 辅音占比 >75%（如 whni: 3辅音/4字母=75%）
    vowels = sum(1 for c in word if c in 'aeiou')
    if len(word) >= 3 and vowels / len(word) <= 0.25:
        return True, f'辅音占比过高 ({vowels}/{len(word)}元音)'

    return False, ''


def extract_words(findings_data):
    """从 findings 中提取所有罗马字词及其频率。

    Args:
        findings_data: unified_scanner 输出的 dict（含 findings 键）

    Returns:
        defaultdict: word → {'count': int, 'episodes': set, 'examples': list}
    """
    word_stats = defaultdict(lambda: {'count': 0, 'episodes': set(), 'examples': []})
    findings_by_type = findings_data.get('findings', findings_data)

    for gtype in ('pure_romaji', 'mixed_romaji'):
        for item in findings_by_type.get(gtype, []):
            text = item['text'].strip()
            words = re.findall(r'[a-zA-Z]{2,}', text)
            ep = extract_ep_number(item['file'])
            for w in words:
                wl = w.lower()
                if len(wl) < MIN_WORD_LEN:
                    continue
                word_stats[wl]['count'] += 1
                word_stats[wl]['episodes'].add(ep)
                if len(word_stats[wl]['examples']) < 3:
                    word_stats[wl]['examples'].append(text[:80])

    return word_stats


def classify(word_stats, min_freq=MIN_FREQ):
    """将词分为 auto（内置词典命中）、pending（待人工填写）、noise（疑似噪声）。

    Args:
        word_stats: extract_words() 的输出
        min_freq: 最低出现次数阈值

    Returns:
        (auto, pending, noise): 三个 dict
    """
    auto = {}
    pending = {}
    noise = {}

    for word, stats in sorted(word_stats.items(), key=lambda x: -x[1]['count']):
        if stats['count'] < min_freq:
            continue
        if word in ENGLISH_OK or word in AI_NOISE:
            continue

        episodes = sorted(list(stats['episodes']))

        # 检查是否为噪声
        is_noise, reason = is_likely_noise(word)
        if is_noise:
            noise[word] = {
                'count': stats['count'],
                'episodes': episodes,
                'examples': stats['examples'][:3],
                'reason': reason,
            }
            continue

        if word in ROMAJI_CORRECTIONS:
            auto[word] = {
                'replacement': ROMAJI_CORRECTIONS[word],
                'count': stats['count'],
                'episodes': episodes,
            }
        elif word in ROMAJI_TO_KANA:
            auto[word] = {
                'replacement': ROMAJI_TO_KANA[word],
                'count': stats['count'],
                'episodes': episodes,
            }
        else:
            pending[word] = {
                'count': stats['count'],
                'episodes': episodes,
                'examples': stats['examples'][:3],
                'replacement': '',
            }

    return auto, pending, noise


def main():
    setup_windows_utf8()
    parser = argparse.ArgumentParser(
        description='项目词典生成器 — 从扫描结果提取高频罗马字词',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 首次生成（默认 min-freq=2，跳过单次出现的词和疑似噪声）
  python build_project_dict.py --findings findings.json --output project_dict.json

  # 查看所有词包括单次出现（调试用）
  python build_project_dict.py --findings findings.json --output project_dict.json --min-freq 1

  # 保留噪声（不自动分类）
  python build_project_dict.py --findings findings.json --output project_dict.json --keep-noise

输出 project_dict.json 后：
  1. 查看 auto 区域 — 内置词典已自动填充，可直接使用
  2. 查看 pending 区域 — 高频但未识别的词，按频率排序
  3. 填写 pending 中需要替换的词的 replacement 字段
  4. noise 区域默认不参与修复，确认有意义的词可手动移入 pending 或 auto
  5. 运行 romaji_fixer.py --project-dict project_dict.json
        """
    )
    parser.add_argument('--findings', required=True,
                        help='unified_scanner.py 输出的 findings.json')
    parser.add_argument('--output', required=True,
                        help='输出的 project_dict.json 路径（建议放在项目根目录）')
    parser.add_argument('--min-freq', type=int, default=MIN_FREQ,
                        help=f'最低出现次数（默认 {MIN_FREQ}）')
    parser.add_argument('--keep-noise', action='store_true',
                        help='保留疑似噪声在 pending 中（不自动分类到 noise 段）')
    args = parser.parse_args()

    with open(args.findings, 'r', encoding='utf-8') as f:
        data = json.load(f)

    word_stats = extract_words(data)
    auto, pending, noise = classify(word_stats, min_freq=args.min_freq)

    # --keep-noise：将噪声合并回 pending
    if args.keep_noise:
        for word, info in noise.items():
            pending[word] = {
                'count': info['count'],
                'episodes': info['episodes'],
                'examples': info.get('examples', [])[:3],
                'replacement': '',
            }
        noise = {}

    output = {'auto': auto, 'pending': pending, 'noise': noise}
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ── 摘要 ──
    print(f'项目词典: {args.output}')
    print(f'  自动匹配: {len(auto)} 词')
    for word, info in sorted(auto.items(), key=lambda x: -x[1]['count']):
        print(f'    {word} → {info["replacement"]} '
              f'({info["count"]}次, {len(info["episodes"])}集)')

    print(f'  待审查: {len(pending)} 词')
    for word, info in sorted(pending.items(), key=lambda x: -x[1]['count'])[:20]:
        print(f'    {word} ({info["count"]}次, {len(info["episodes"])}集) '
              f'— {info["examples"][0][:50]}')
    if len(pending) > 20:
        print(f'    ... 还有 {len(pending) - 20} 词，见 {args.output}')

    if noise:
        print(f'  疑似噪声（已跳过）: {len(noise)} 词')
        for word, info in sorted(noise.items(), key=lambda x: -x[1]['count'])[:10]:
            print(f'    {word} ({info["count"]}次) — {info["reason"]}')
        if len(noise) > 10:
            print(f'    ... 还有 {len(noise) - 10} 词，见 {args.output}')
        print(f'  → 确认有意义的词可手动移入 pending/auto，或用 --keep-noise 保留全部')


if __name__ == '__main__':
    main()
