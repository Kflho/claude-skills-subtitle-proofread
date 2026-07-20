#!/usr/bin/env python3
"""脚本: Name 字段扫描与语言分类检测。

扫描所有 Dialogue 行的 Name 字段，收集唯一值并按语言分类。
标记非目标语言（非 CJK）的 Name 值供 Claude 审查。

用法:
  python names_detect.py --target-dir <DIR>                          # JSON 输出
  python names_detect.py --target-dir <DIR> --scan                   # 打印映射模板
  python names_detect.py --target-dir <DIR> --lang-check             # 语言分类检测
  python names_detect.py --target-dir <DIR> --lang-check --target-lang cjk  # 指定目标语言
"""

import argparse
import json
import re
import sys
import os
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import parse_dialogue, read_ass_file, iter_ass_files

# ═══════════════════════════════════════════════════════════════
# 语言分类器
# ═══════════════════════════════════════════════════════════════

LANG_CLASSIFIERS = {
    'cjk': {
        'name': 'CJK (中日韩)',
        'pattern': re.compile(r'[一-鿿]'),
        'is_target': True,
    },
    'japanese_kana': {
        'name': '日语假名',
        'pattern': re.compile(r'[぀-ゟ゠-ヿ･-ﾟ]'),
        'is_target': False,
    },
    'cyrillic': {
        'name': '西里尔字母 (俄语等)',
        'pattern': re.compile(r'[А-Яа-яЁё]'),
        'is_target': False,
    },
    'latin': {
        'name': '拉丁字母 (英语等)',
        'pattern': re.compile(r'[A-Za-z]'),
        'is_target': False,
    },
    'digits': {
        'name': '数字',
        'pattern': re.compile(r'[0-9]'),
        'is_target': True,  # 数字通常是目标语言的一部分
    },
    'punctuation': {
        'name': '标点/符号',
        'pattern': re.compile(r'[ 　,，.。!！?？:：;；\-—・&＆·\s]'),
        'is_target': True,
    },
}


def classify_name(name: str) -> dict:
    """分类一个 Name 值的主要语言。

    Args:
        name: Name 字段值

    Returns:
        {
            "name": 原始名称,
            "primary_language": "cjk" | "cyrillic" | "latin" | "japanese_kana" | "mixed" | "unknown",
            "languages_detected": ["cjk", "latin", ...],
            "is_non_target": True/False,  # 是否包含非目标语言字符
            "non_target_chars": [...],  # 非目标语言字符示例
        }
    """
    if not name or not name.strip():
        return {
            'name': name,
            'primary_language': 'empty',
            'languages_detected': [],
            'is_non_target': False,
            'non_target_chars': [],
        }

    detected = []
    non_target_chars = set()

    for lang_key, classifier in LANG_CLASSIFIERS.items():
        matches = classifier['pattern'].findall(name)
        if matches:
            detected.append(lang_key)
            if not classifier['is_target']:
                non_target_chars.update(matches)

    # 判断主要语言
    if not detected:
        primary = 'unknown'
    elif len(detected) == 1:
        primary = detected[0]
    else:
        # 多种字符混合：看哪种最多
        counts = {}
        for lang_key in detected:
            counts[lang_key] = len(LANG_CLASSIFIERS[lang_key]['pattern'].findall(name))
        primary = max(counts, key=counts.get)
        if len(detected) > 1 and primary in ('punctuation', 'digits'):
            # 如果主要是标点/数字，看第二多的是什么
            remaining = {k: v for k, v in counts.items() if k not in ('punctuation', 'digits')}
            if remaining:
                primary = max(remaining, key=remaining.get)

    # 判断是否为非目标语言
    non_target_langs = [d for d in detected
                        if d in LANG_CLASSIFIERS and not LANG_CLASSIFIERS[d]['is_target']]

    return {
        'name': name,
        'primary_language': primary,
        'languages_detected': detected,
        'is_non_target': len(non_target_langs) > 0,
        'non_target_languages': non_target_langs,
        'non_target_chars': sorted(non_target_chars)[:20],
    }


def collect_all_names(target_dir: str) -> tuple[list[str], dict[str, list[str]]]:
    """收集所有 Name 字段值。"""
    all_names = set()
    by_file = {}

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        file_names = []
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            name = d['name'].strip()
            if name:
                all_names.add(name)
                file_names.append(name)
        if file_names:
            seen = set()
            unique_file_names = []
            for n in file_names:
                if n not in seen:
                    seen.add(n)
                    unique_file_names.append(n)
            by_file[fname] = unique_file_names
        else:
            by_file[fname] = []

    return sorted(all_names), by_file


def scan_names(target_dir: str):
    """扫描并输出所有 Name 字段值。"""
    names, by_file = collect_all_names(target_dir)

    # 分类每个名称
    classified = {name: classify_name(name) for name in names}

    non_target = {n: c for n, c in classified.items() if c['is_non_target']}
    target = {n: c for n, c in classified.items() if not c['is_non_target'] and c['primary_language'] != 'empty'}

    print(f'=== Name 字段扫描结果 ===')
    print(f'共 {len(names)} 个不同的 Name 值')
    print(f'  目标语言 (CJK): {len(target)} 个')
    print(f'  非目标语言 (需审查): {len(non_target)} 个')
    print()

    if non_target:
        print('--- 非目标语言的 Name 值（需翻译）---')
        for name, info in sorted(non_target.items(), key=lambda x: x[1]['primary_language']):
            lang_names = [LANG_CLASSIFIERS[l]['name'] for l in info['non_target_languages']]
            print(f"  [{info['primary_language']}] \"{name}\"  (含: {', '.join(lang_names)})")

    print()
    print('--- JSON 映射模板（非目标语言部分）---')
    print('{')
    for name in sorted(non_target.keys()):
        print(f'    "{name}": "",')
    print('}')


def lang_check(target_dir: str, target_lang: str = 'cjk') -> dict:
    """语言分类检测模式。"""
    names, by_file = collect_all_names(target_dir)
    classified = {name: classify_name(name) for name in names}

    # 按语言分组
    by_language = defaultdict(list)
    for name, info in classified.items():
        by_language[info['primary_language']].append({
            'name': name,
            'languages_detected': info['languages_detected'],
            'non_target_languages': info['non_target_languages'],
            'non_target_chars': info['non_target_chars'],
        })

    non_target = {n: c for n, c in classified.items() if c['is_non_target']}

    # 统计非目标语言 Name 在文件中的出现频率
    name_line_counts = Counter()
    name_file_counts = defaultdict(set)
    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        for line in lines:
            d = parse_dialogue(line)
            if d is None:
                continue
            name = d['name'].strip()
            if name and name in non_target:
                name_line_counts[name] += 1
                name_file_counts[name].add(fname)

    # 按出现频率排序非目标语言 Name
    non_target_with_stats = []
    for name, info in non_target.items():
        non_target_with_stats.append({
            'name': name,
            'primary_language': info['primary_language'],
            'non_target_languages': info['non_target_languages'],
            'non_target_chars': info['non_target_chars'],
            'occurrences': name_line_counts[name],
            'files': sorted(name_file_counts[name]),
            'file_count': len(name_file_counts[name]),
        })

    non_target_with_stats.sort(key=lambda x: -x['occurrences'])

    return {
        'total_names': len(names),
        'target_language_names': len(names) - len(non_target),
        'non_target_names': len(non_target),
        'non_target_by_language': {
            lang: len(items) for lang, items in by_language.items()
            if lang not in ('cjk', 'digits', 'punctuation', 'empty', 'unknown')
        },
        'non_target_names_detail': non_target_with_stats,
        'by_file': by_file,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Name 字段扫描与语言分类检测',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本扫描
  python names_detect.py --target-dir ./target/

  # 打印映射模板（含语言分类）
  python names_detect.py --target-dir ./target/ --scan

  # 语言分类检测（JSON 输出）
  python names_detect.py --target-dir ./target/ --lang-check
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--scan', action='store_true',
                        help='扫描并打印 Name 字段映射模板（含语言分类）')
    parser.add_argument('--lang-check', action='store_true',
                        help='语言分类检测模式')
    parser.add_argument('--target-lang', default='cjk',
                        help='目标语言代码 (默认: cjk)')
    args = parser.parse_args()

    if args.scan:
        scan_names(args.target_dir)
        return

    if args.lang_check:
        result = lang_check(args.target_dir, args.target_lang)
        print(f"发现 {result['non_target_names']} 个非目标语言 Name 值",
              file=sys.stderr)
        for lang, count in result['non_target_by_language'].items():
            lang_name = LANG_CLASSIFIERS.get(lang, {}).get('name', lang)
            print(f"  {lang_name}: {count} 个", file=sys.stderr)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')
        return

    # 默认：基本扫描
    names, by_file = collect_all_names(args.target_dir)
    result = {
        'names': names,
        'by_file': by_file,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
