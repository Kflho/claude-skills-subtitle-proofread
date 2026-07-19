#!/usr/bin/env python3
"""脚本: 双语混合行检测。

检测对话行中的多语言混合情况：
- mixed: 同时包含目标语言（CJK）和源语言（如英文）字符
- pure_source: 纯源语言行（无目标语言字符）
用法: python bilingual_detect.py --target-dir <DIR> [--config <CONFIG.json>]
"""

import argparse
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, contains_cjk,
    read_ass_file, iter_ass_files, iter_dialogue_lines
)


def has_source_lang_chars(text: str, source_pattern: re.Pattern) -> bool:
    """检查文本是否包含源语言字符。"""
    return bool(source_pattern.search(text))


def has_consecutive_source(text: str, source_pattern: re.Pattern, min_len: int = 3) -> bool:
    """检查文本是否包含 min_len 个以上连续的源语言字符。"""
    pattern_str = source_pattern.pattern
    consecutive_re = re.compile(f'(?:{pattern_str}){{{min_len},}}')
    return bool(consecutive_re.search(text))


def main():
    parser = argparse.ArgumentParser(description='双语混合行检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', type=str, default=None,
                        help='JSON 配置文件（可选，用于指定 dialogue_styles 和 source_lang_pattern）')
    args = parser.parse_args()

    # 默认配置
    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    source_lang_pattern = r'[A-Za-z]'  # 默认识别英文

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        if 'dialogue_styles' in config:
            dialogue_styles = set(config['dialogue_styles'])
        if 'source_lang_pattern' in config:
            source_lang_pattern = config['source_lang_pattern']

    source_re = re.compile(source_lang_pattern)

    findings = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for line_idx, d in iter_dialogue_lines(lines, dialogue_styles):
            # 跳过绘图指令行
            if '\\p1' in d['text']:
                continue

            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            has_cjk = contains_cjk(visible)
            has_source = has_source_lang_chars(visible, source_re)
            has_3plus_source = has_consecutive_source(visible, source_re, 3)

            if has_cjk and has_3plus_source:
                # 目标语言 + 源语言同时存在 → 混合
                findings.append({
                    'file': fname,
                    'line': line_idx + 1,
                    'type': 'mixed',
                    'text': d['text'],
                    'visible': visible,
                    'timecode': d['start'],
                })
            elif not has_cjk and has_source:
                # 无目标语言但有源语言 → 纯源语言行
                findings.append({
                    'file': fname,
                    'line': line_idx + 1,
                    'type': 'pure_source',
                    'text': d['text'],
                    'visible': visible,
                    'timecode': d['start'],
                })

    json.dump(findings, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
