#!/usr/bin/env python3
"""脚本 11: 固定格式不一致检测。

扫描预告标题、结尾语、转场提示等固定格式短语的变体。
输出所有匹配到的变体，供 Claude 审查统一。

用法: python format_detect.py --target-dir <DIR> --config <CONFIG.json>
"""

import argparse
import json
import re
import sys
import os
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, parse_dialogue,
    read_ass_file, iter_ass_files
)


# 内置常见固定格式模式（可配置）
DEFAULT_PATTERNS = {
    '预告标题': [
        r'下集预告',
        r'下一集',
        r'下集[，,]预告',
        r'在下一集中',
        r'下一个系列',
        r'请看下集',
        r'次回予告',
        r'予告',
    ],
    '结尾语': [
        r'敬请期待',
        r'敬请收看',
        r'敬请观赏',
        r'千万不要错过',
        r'不要错过',
        r'请欣赏',
        r'下周再见',
        r'下次再见',
    ],
    '转场提示': [
        r'与此同时',
        r'另一方面',
        r'稍后',
        r'之后',
        r'几天后',
        r'第二天',
        r'同[一时]时间',
        r'当天晚上',
        r'第二天早上',
    ],
    '编辑标记': [
        r'\[.*?\]',         # [xxx] 方括号标记
        r'[≪≫《》].*?[≪≫《》]',  # 特殊括号标记
        r'^[\(（].*?[\)）]$',  # 纯括号内容
    ],
}


def main():
    parser = argparse.ArgumentParser(description='固定格式不一致检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=False, help='JSON 配置文件（可选：自定义格式模式）')
    parser.add_argument('--scan', action='store_true', help='扫描独特短语而非匹配已知模式')
    args = parser.parse_args()

    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    patterns = dict(DEFAULT_PATTERNS)

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        dialogue_styles = set(config.get('dialogue_styles', dialogue_styles))
        if 'patterns' in config:
            patterns.update(config['patterns'])

    findings = defaultdict(list)
    variant_counts = defaultdict(Counter)

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)

        for i, line in enumerate(lines, 1):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['style'] not in dialogue_styles:
                continue

            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            for category, pat_list in patterns.items():
                for pat in pat_list:
                    m = re.search(pat, visible)
                    if m:
                        matched_text = m.group(0)
                        variant_counts[category][matched_text] += 1
                        findings[category].append({
                            'file': fname,
                            'line': i,
                            'matched': matched_text,
                            'full_text': visible[:80],
                            'timecode': d['start'],
                        })
                        break  # 每个类别只匹配一次

    # 每个类别输出变体统计
    output = {
        'variant_stats': {
            cat: dict(counter.most_common(20))
            for cat, counter in variant_counts.items()
        },
        'findings': dict(findings),
        'total_findings': sum(len(v) for v in findings.values()),
    }

    for cat, counter in variant_counts.items():
        print(f'\n[{cat}] 发现 {len(counter)} 种变体:', file=sys.stderr)
        for variant, count in counter.most_common(10):
            print(f'  {variant}: {count} 次', file=sys.stderr)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
