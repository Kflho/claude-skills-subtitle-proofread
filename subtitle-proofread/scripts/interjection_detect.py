#!/usr/bin/env python3
"""脚本 4: 感叹词/语气词残留检测。

扫描对话中残留的源语言短词（2-5 个源语言字符的独立词）。
输出 JSON 供 Claude 审查，确认后生成 fixes.json 用 apply_fixes.py 修复。

用法: python interjection_detect.py --target-dir <DIR> --config <CONFIG.json>
"""

import argparse
import json
import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import (
    strip_ass_tags, parse_dialogue,
    read_ass_file, iter_ass_files
)


def main():
    parser = argparse.ArgumentParser(description='感叹词/语气词残留检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=False, help='JSON 配置文件')
    args = parser.parse_args()

    # 默认配置
    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    source_char_pattern = r'[A-Za-z]'  # 检测英文/拉丁字符（可改为俄语等）
    min_len = 2
    max_len = 5

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        dialogue_styles = set(config.get('dialogue_styles', dialogue_styles))
        source_char_pattern = config.get('source_char_pattern', source_char_pattern)
        min_len = config.get('min_len', min_len)
        max_len = config.get('max_len', max_len)

    source_re = re.compile(source_char_pattern)

    # 收集所有源语言短词及其上下文
    word_counter = Counter()
    findings = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)

        for i, line in enumerate(lines, 1):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['style'] not in dialogue_styles:
                continue
            if '\\p1' in d['text']:
                continue

            visible = strip_ass_tags(d['text'])
            if not visible.strip():
                continue

            # 找到可见文本中的源语言词
            words = re.findall(rf'\b[{source_char_pattern}]{{{min_len},{max_len}}}\b', visible, re.IGNORECASE)
            # 也匹配非 ASCII 源语言字符词（俄语等）
            words_unicode = re.findall(rf'[{source_char_pattern}]{{{min_len},{max_len}}}', visible)

            for w in set(words + words_unicode):
                word_counter[w] += 1
                findings.append({
                    'file': fname,
                    'line': i,
                    'word': w,
                    'context': visible[:80],
                    'timecode': d['start'],
                    'style': d['style'],
                })

    # 按频率排序输出（高频词优先审查）
    findings.sort(key=lambda f: (-word_counter[f['word']], f['file'], f['line']))

    output = {
        'word_frequencies': dict(word_counter.most_common(50)),
        'findings': findings[:500],  # 最多 500 条，避免输出过大
        'total_findings': len(findings),
    }

    print(f'共发现 {len(findings)} 处疑似感叹词残留', file=sys.stderr)
    print(f'去重后 {len(word_counter)} 个不同词', file=sys.stderr)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
