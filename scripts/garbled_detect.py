#!/usr/bin/env python3
"""脚本 10: 机翻幻觉/乱码检测。

扫描可能的 MT 幻觉模式：
- 知名人物名/角色名出现在对话中（人名→名人幻觉）
- 动物名→花名/物名误译
- 假朋友（False Friend）
- 其他可疑的翻译模式

输出 JSON 供 Claude 审查，确认后生成 fixes.json 用 apply_fixes.py 修复。

用法: python garbled_detect.py --target-dir <DIR> --config <CONFIG.json>
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


# 内置可疑模式库（可按需扩展）
SUSPICIOUS_PATTERNS = [
    # (正则模式, 类别, 说明)
    # 日→中幻觉示例
    (r'安倍晋三', '人名幻觉', '普通人名被译为日本前首相'),
    (r'龟仙人', '角色幻觉', '普通人名被译为龙珠角色'),
    (r'布尔玛', '角色幻觉', '普通人名被译为龙珠角色'),
    (r'孙悟空', '角色幻觉', '普通人名被译为西游记/龙珠角色'),
    (r'路飞', '角色幻觉', '普通人名被译为海贼王角色'),
    (r'鸣人', '角色幻觉', '普通人名被译为火影角色'),
    # 英→中幻觉
    (r'特朗普', '人名幻觉', '普通人名被译为美国总统'),
    (r'奥巴马', '人名幻觉', '普通人名被译为美国总统'),
    (r'爱因斯坦', '人名幻觉', '普通人名被译为科学家'),
    # 脏话误译
    (r'去你妈的', '脏话误译', '可能为责备用语的过度翻译'),
    (r'他妈的', '脏话误译', '可能为语气词的过度翻译'),
    (r'操你', '脏话误译', '可能为感叹词的过度翻译'),
    (r'你妈的', '脏话误译', '可能为语气词的过度翻译'),
    # 惯用语字面直译
    (r'我放弃你', '惯用语直译', 'I give you up 的直译'),
    (r'给我上', '惯用语直译', '可能为 give me up 的直译'),
]


def main():
    parser = argparse.ArgumentParser(description='机翻幻觉/乱码检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', required=False, help='JSON 配置文件（可选：添加自定义可疑模式）')
    args = parser.parse_args()

    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    custom_patterns = []

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        dialogue_styles = set(config.get('dialogue_styles', dialogue_styles))
        custom_patterns = config.get('custom_patterns', [])

    all_patterns = SUSPICIOUS_PATTERNS + custom_patterns

    findings = []
    pattern_hits = {p[0]: 0 for p in all_patterns}

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)

        for i, line in enumerate(lines, 1):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['style'] not in dialogue_styles:
                continue

            visible = strip_ass_tags(d['text'])

            for pattern, category, description in all_patterns:
                if re.search(pattern, visible):
                    findings.append({
                        'file': fname,
                        'line': i,
                        'pattern': pattern,
                        'category': category,
                        'description': description,
                        'text': d['text'][:100],
                        'visible': visible[:100],
                        'timecode': d['start'],
                    })
                    pattern_hits[pattern] += 1

    # 按类别分组
    by_category = {}
    for f in findings:
        cat = f['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(f)

    output = {
        'summary': {p: {'count': c, 'category': cat, 'desc': desc}
                    for (p, cat, desc), c in pattern_hits.items() if c > 0},
        'by_category': by_category,
        'total_findings': len(findings),
    }

    for p, c in pattern_hits.items():
        if c > 0:
            print(f'  {p}: {c} 处', file=sys.stderr)
    print(f'\n共发现 {len(findings)} 处可疑模式', file=sys.stderr)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
