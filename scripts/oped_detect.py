#!/usr/bin/env python3
"""脚本: OP/ED 歌词轨对比检测。

比较两个 style 的歌词轨道，按时间码容差匹配，
报告文本不一致的行，用于发现机翻乱码轨。
用法: python oped_detect.py --target-dir <DIR> --config <CONFIG.json>
输出: JSON 数组到 stdout
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    time_to_ms, parse_dialogue,
    read_ass_file, iter_ass_files
)


def main():
    parser = argparse.ArgumentParser(description='OP/ED 歌词轨对比检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=True, help='JSON 配置文件')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    source_style = config['source_style']
    ref_style = config['ref_style']
    tolerance_ms = config.get('tolerance_ms', 500)

    results = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)

        # 构建参考轨时间码→文本映射
        ref_map = {}
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d and d['style'] == ref_style:
                ref_map[time_to_ms(d['start'])] = d['text']

        if not ref_map:
            continue

        # 构建源轨时间码→文本映射
        source_map = {}
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d and d['style'] == source_style:
                source_map[time_to_ms(d['start'])] = d['text']

        # 按容差匹配，报告文本不同的行
        for start_ms, source_text in source_map.items():
            best_ref_ms = None
            best_distance = float('inf')
            for ref_ms in ref_map:
                dist = abs(start_ms - ref_ms)
                if dist <= tolerance_ms and dist < best_distance:
                    best_ref_ms = ref_ms
                    best_distance = dist

            if best_ref_ms is not None and ref_map[best_ref_ms] != source_text:
                # 找到对应行的行号
                line_no = None
                for i, line in enumerate(lines):
                    d = parse_dialogue(line)
                    if d and d['style'] == source_style and time_to_ms(d['start']) == start_ms:
                        line_no = i + 1  # 1-based
                        break

                results.append({
                    'file': fname,
                    'line': line_no,
                    'start_ms': start_ms,
                    'source_text': source_text,
                    'ref_text': ref_map[best_ref_ms],
                    'style': source_style,
                })

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
