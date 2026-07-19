#!/usr/bin/env python3
"""脚本: 纯源语言行检测。

检测整行只有源语言字符、没有目标语言字符的对话行。
用法: python source_lang_detect.py --target-dir <DIR> --config <CONFIG.json>
输出: JSON 数组到 stdout
"""

import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, contains_cjk,
    parse_dialogue, read_ass_file, iter_ass_files
)


def main():
    parser = argparse.ArgumentParser(description='纯源语言行检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=True, help='JSON 配置文件')
    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)

    dialogue_styles = set(config.get('dialogue_styles', ['Default']))
    source_char_pattern = re.compile(config['source_char_pattern'])
    target_lang = config.get('target_lang', 'cjk')

    def has_source_chars(text: str) -> bool:
        return bool(source_char_pattern.search(text))

    def has_target_lang_chars(text: str) -> bool:
        if target_lang == 'cjk':
            return contains_cjk(text)
        return bool(re.search(target_lang, text))

    results = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['style'] not in dialogue_styles:
                continue

            # 跳过空行和绘图行
            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue
            if '\\p1' in d['text']:
                continue

            # 纯源语言行：有源语言字符但没有目标语言字符
            if has_source_chars(visible) and not has_target_lang_chars(visible):
                results.append({
                    'file': fname,
                    'line': i + 1,  # 1-based
                    'text': d['text'],
                    'visible': visible,
                    'timecode': d['start'],
                    'style': d['style'],
                })

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
