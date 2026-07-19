#!/usr/bin/env python3
"""脚本 5: 专有名词变体检测。

扫描字幕中可能的专有名词及其译名变体。
检测策略：
1. 收集所有含专名特征的行（大写字母词、引号内容等）
2. 按上下文聚类，帮助 Claude 发现同一实体的多个译名

用法: python proper_noun_detect.py --target-dir <DIR> --ref-dir <REF_DIR> [--config <CONFIG.json>]
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


def extract_proper_nouns_cjk(text: str) -> list[str]:
    """从中文文本中提取可能的专有名词（2-5 个汉字的人名/地名）。"""
    # 常见专名特征词尾
    name_suffixes = r'(?:先生|小姐|女士|老师|同学|医生|队长|警长|校长|国王|王子|公主|殿下|大人|君|酱|ちゃん|さん|くん)'
    # 带称谓的人名
    names = re.findall(rf'(.{{1,5}}?)(?:{name_suffixes})', text)
    # 引号/书名号中的内容
    quoted = re.findall(r'[「「]([^」」]{1,10})[」」]', text)
    quoted += re.findall(r'[《]([^》]{1,15})[》]', text)
    return names + quoted


def extract_source_proper_nouns(text: str, source_pattern: str) -> list[str]:
    """从源语言文本中提取可能的专有名词（大写开头词）。"""
    # 匹配大写开头的连续字母词
    names = re.findall(r'\b([A-ZА-ЯЁ][a-zа-яё]{2,})\b', text)
    return names


def main():
    parser = argparse.ArgumentParser(description='专有名词变体检测')
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录（机翻）')
    parser.add_argument('--ref-dir', required=False, help='参考字幕目录（人工翻译，用于对比译名一致性）')
    parser.add_argument('--config', required=False, help='JSON 配置文件')
    args = parser.parse_args()

    dialogue_styles = {'Default', 'DefaultTop', 'Episode'}
    source_char_pattern = r'[A-Za-zА-Яа-яЁё]'  # 源语言字符

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)
        dialogue_styles = set(config.get('dialogue_styles', dialogue_styles))
        source_char_pattern = config.get('source_char_pattern', source_char_pattern)

    # 1. 从机翻字幕收集中文专名
    target_nouns = Counter()
    target_noun_contexts = defaultdict(list)

    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines, 1):
            d = parse_dialogue(line)
            if d is None or d['style'] not in dialogue_styles:
                continue
            visible = strip_ass_tags(d['text'])
            nouns = extract_proper_nouns_cjk(visible)
            for n in nouns:
                target_nouns[n] += 1
                target_noun_contexts[n].append({
                    'file': fname,
                    'line': i,
                    'context': visible[:80],
                })

    # 2. 从机翻字幕收集源语言专名（Name 字段）
    source_names = Counter()
    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        for i, line in enumerate(lines, 1):
            d = parse_dialogue(line)
            if d is None:
                continue
            if d['name']:
                source_names[d['name']] += 1

    # 3. 如果提供了参考字幕，从中提取标准译名
    ref_nouns = Counter()
    if args.ref_dir and os.path.isdir(args.ref_dir):
        for fname, fpath in iter_ass_files(args.ref_dir):
            lines = read_ass_file(fpath)
            for i, line in enumerate(lines, 1):
                d = parse_dialogue(line)
                if d is None:
                    continue
                visible = strip_ass_tags(d['text'])
                nouns = extract_proper_nouns_cjk(visible)
                for n in nouns:
                    ref_nouns[n] += 1

    # 4. 输出
    # 按频率排序
    output = {
        'target_proper_nouns': [
            {'noun': n, 'count': c, 'samples': target_noun_contexts[n][:3]}
            for n, c in target_nouns.most_common(100)
        ],
        'source_names': [
            {'name': n, 'count': c}
            for n, c in source_names.most_common(100)
        ],
        'ref_proper_nouns': [
            {'noun': n, 'count': c}
            for n, c in ref_nouns.most_common(100)
        ] if args.ref_dir else [],
        'target_has_ref': [n for n in target_nouns if n in ref_nouns],
        'target_no_ref': [n for n, c in target_nouns.most_common(50) if n not in ref_nouns],
        'ref_no_target': [n for n, c in ref_nouns.most_common(50) if n not in target_nouns] if args.ref_dir else [],
    }

    print(f'机翻字幕专名: {len(target_nouns)} 种', file=sys.stderr)
    print(f'Name 字段: {len(source_names)} 种', file=sys.stderr)
    if args.ref_dir:
        print(f'参考字幕专名: {len(ref_nouns)} 种', file=sys.stderr)
        print(f'仅在机翻中出现: {len(output["target_no_ref"])} 种（可能是幻觉）', file=sys.stderr)
        print(f'仅在参考中出现: {len(output["ref_no_target"])} 种（可能是漏译）', file=sys.stderr)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
