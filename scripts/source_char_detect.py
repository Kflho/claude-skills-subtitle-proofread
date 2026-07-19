#!/usr/bin/env python3
"""脚本: 残留源语言字符检测。

扫描对话行中残留的源语言字符（如俄文字母、英文字母等）。
用法: python source_char_detect.py --target-dir <DIR> --config <CONFIG.json>
输出: JSON 数组到 stdout
"""

import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, parse_dialogue,
    read_ass_file, iter_ass_files
)


def main():
    parser = argparse.ArgumentParser(description='残留源语言字符检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=True, help='JSON 配置文件')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    dialogue_styles = set(config.get('dialogue_styles', ['Default']))
    source_char_pattern = re.compile(config['source_char_pattern'])

    results = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['style'] not in dialogue_styles:
                continue

            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            # 查找所有源语言字符匹配
            chars_found = source_char_pattern.findall(visible)
            if chars_found:
                results.append({
                    'file': fname,
                    'line': i + 1,  # 1-based
                    'timecode': d['start'],
                    'visible': visible,
                    'source_chars_found': chars_found,
                    'count': len(chars_found),
                })

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
