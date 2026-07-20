#!/usr/bin/env python3
r"""脚本: ASS 矢量绘图命令行检测。

扫描包含 \p1 或 \p0 标签的行，检测 \p1 行中是否存在
非绘图命令的可疑字符（如 CJK 字符、翻译残留文字等）。
用法: python drawing_detect.py --target-dir <DIR>
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

# 合法的绘图命令字符：字母 (m, n, l, b, s, p, c) + 数字 + 空格 + 小数点 + 负号
DRAWING_CMD_RE = re.compile(r'[mnlbspecMNLBSPEC\d\s\.\-]+')


def scan_suspicious_parts(text: str) -> list:
    """扫描文本中非绘图命令的可疑部分。

    先 strip ASS 标签，然后将文本按合法绘图命令字符分割，
    非空且非纯空格的片段即为可疑部分。

    Returns:
        可疑文本片段列表
    """
    visible = strip_ass_tags(text)
    if not visible.strip():
        return []

    # 按连续非法字符拆分，收集可疑片段
    suspicious = []
    # 用 finditer 找出所有非合法绘图命令字符的连续序列
    illegal_pattern = re.compile(r'[^mnlbspecMNLBSPEC\d\s\.\-\{\}]+')
    for match in illegal_pattern.finditer(visible):
        part = match.group().strip()
        if part:
            suspicious.append(part)
    return suspicious


def main():
    parser = argparse.ArgumentParser(description='ASS 矢量绘图命令行检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    args = parser.parse_args()

    results = []

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines):
            d = parse_dialogue(line)
            if d is None:
                continue

            text = d['text']
            # 只检查包含绘图标签的行
            if '\\p1' not in text and '\\p0' not in text:
                continue

            result = {
                'file': fname,
                'line': i + 1,  # 1-based
                'text': text,
            }

            # 对 \p1 行检测可疑字符
            if '\\p1' in text:
                suspicious = scan_suspicious_parts(text)
                result['has_suspicious_chars'] = len(suspicious) > 0
                result['suspicious_parts'] = suspicious
            else:
                # \p0 行没有绘图内容，不需要检测
                result['has_suspicious_chars'] = False
                result['suspicious_parts'] = []

            results.append(result)

    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
