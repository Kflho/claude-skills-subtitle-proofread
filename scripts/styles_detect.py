#!/usr/bin/env python3
"""脚本: 样式使用统计。

扫描所有 Dialogue 行的 Style 字段，统计每个样式的使用次数、
出现文件列表和示例文本，帮助决定处理/删除哪些样式。
用法: python styles_detect.py --target-dir <DIR>
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, parse_dialogue, read_ass_file, iter_ass_files
)


def collect_style_stats(target_dir: str) -> list[dict]:
    """收集所有样式使用统计。

    Returns:
        按 count 降序排列的样式统计列表
    """
    # style -> {'count': int, 'files': set, 'sample_text': str or None}
    stats: dict[str, dict] = {}

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            style = d['style']
            if style not in stats:
                stats[style] = {
                    'count': 0,
                    'files': set(),
                    'sample_text': None,
                }
            entry = stats[style]
            entry['count'] += 1
            entry['files'].add(fname)

            # 记录第一条示例文本（前 30 字）
            if entry['sample_text'] is None:
                visible = strip_ass_tags(d['text']).strip()
                if visible:
                    entry['sample_text'] = visible[:30]

    # 转换为输出格式，按 count 降序排列
    result = []
    for style, entry in sorted(stats.items(), key=lambda x: -x[1]['count']):
        result.append({
            'style': style,
            'count': entry['count'],
            'sample_text': entry['sample_text'] or '',
            'files': sorted(entry['files']),
        })

    return result


def main():
    parser = argparse.ArgumentParser(description='样式使用统计')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    args = parser.parse_args()

    results = collect_style_stats(args.target_dir)

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
