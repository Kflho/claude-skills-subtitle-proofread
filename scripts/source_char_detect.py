#!/usr/bin/env python3
"""脚本: 残留源语言字符检测（多语言版）。

扫描对话行中残留的源语言字符，支持英语、日语假名、俄语西里尔字母等多语言模式。
每种语言独立检测并分类输出，供 Claude 审查后生成 fixes.json。

用法:
  # 单语言（向后兼容）
  python source_char_detect.py --target-dir <DIR> --config <CONFIG.json>

  # 多语言（使用内建预设）
  python source_char_detect.py --target-dir <DIR> --langs en,jp,ru

  # 仅检测 Name 字段
  python source_char_detect.py --target-dir <DIR> --langs en,jp,ru --mode names

  # 仅检测 Text 字段
  python source_char_detect.py --target-dir <DIR> --langs en,jp,ru --mode text

输出: JSON 到 stdout，按语言分组
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
    read_ass_file, iter_ass_files, iter_dialogue_lines
)

# ═══════════════════════════════════════════════════════════════
# 内建语言预设
# ═══════════════════════════════════════════════════════════════

LANGUAGE_PRESETS = {
    'en': {
        'name': 'English',
        'pattern': r'[A-Za-z]{2,}',       # 2+ 连续拉丁字母
        'description': '英语单词/短语残留',
        'single_char_pattern': r'[A-Za-z]',  # 单字符模式（用于 Name 字段）
    },
    'jp': {
        'name': 'Japanese Kana',
        'pattern': r'[぀-ゟ゠-ヿ･-ﾟ]{1,}',  # 平假名+片假名
        'description': '日语假名残留',
        'single_char_pattern': r'[぀-ゟ゠-ヿ･-ﾟ]',
    },
    'ru': {
        'name': 'Russian Cyrillic',
        'pattern': r'[А-Яа-яЁё]{1,}',      # 西里尔字母
        'description': '俄语西里尔字母残留',
        'single_char_pattern': r'[А-Яа-яЁё]',
    },
    'cjk': {
        'name': 'CJK Ideographs',
        'pattern': r'[一-鿿]{1,}',   # CJK统一表意文字
        'description': '中日韩统一表意文字（目标语言）',
        'single_char_pattern': r'[一-鿿]',
    },
}


def detect(target_dir: str, config: dict = None, mode: str = 'all') -> dict:
    """扫描目标目录，检测残留源语言字符。

    Args:
        target_dir: 目标 ASS 字幕目录
        config: 配置 dict，支持:
            - dialogue_styles: 限定样式集合
            - skip_styles: 跳过样式集合
            - languages: 要检测的语言列表（如 ['en', 'jp', 'ru']）
            - custom_languages: 自定义语言定义 {name: {pattern, description}}
        mode: 'text' | 'names' | 'all'

    Returns:
        {
            "by_language": {lang_code: {"findings": [...], "summary": {...}}},
            "total_findings": N
        }
    """
    if config is None:
        config = {}

    dialogue_styles = set(config.get('dialogue_styles', {'Default', 'DefaultTop', 'Episode'}))
    skip_styles = set(config.get('skip_styles', {'Display', 'Title 1', 'Title 2',
                                                   'Opening Romaji', 'Opening Rus', 'Roboto'}))

    # 构建语言检测器列表
    languages = {}
    lang_keys = config.get('languages', [])

    # 向后兼容：单语言模式
    if not lang_keys and 'source_char_pattern' in config:
        languages['custom'] = {
            'name': 'Custom',
            'pattern': config['source_char_pattern'],
            'description': '自定义源语言字符',
        }
    else:
        for key in lang_keys:
            if key in LANGUAGE_PRESETS:
                languages[key] = dict(LANGUAGE_PRESETS[key])

    # 合并自定义语言
    for name, defn in config.get('custom_languages', {}).items():
        languages[name] = defn

    if not languages:
        # 默认：英语
        languages['en'] = dict(LANGUAGE_PRESETS['en'])

    # 编译正则
    for key in languages:
        languages[key]['_pattern_re'] = re.compile(languages[key]['pattern'])
        if 'single_char_pattern' in languages[key]:
            languages[key]['_single_re'] = re.compile(languages[key]['single_char_pattern'])

    results = {}
    check_text = mode in ('text', 'all')
    check_names = mode in ('names', 'all')

    for lang_key, lang_def in languages.items():
        findings = []
        char_counter = Counter()
        name_counter = Counter()
        affected_files = set()

        for fname, fpath in iter_ass_files(target_dir):
            lines = read_ass_file(fpath)
            file_has_findings = False

            for line_idx, d in iter_dialogue_lines(lines):
                style = d['style']
                if style in skip_styles:
                    continue
                if dialogue_styles and style not in dialogue_styles:
                    continue

                matched = False
                finding = {
                    'file': fname,
                    'line': line_idx + 1,
                    'timecode': d['start'],
                    'style': style,
                }

                # 检测 Name 字段
                if check_names and d['name']:
                    name = d['name'].strip()
                    single_re = lang_def.get('_single_re')
                    name_re = lang_def['_pattern_re']

                    if single_re and single_re.search(name):
                        # 单字符匹配（适用于西里尔/假名 Name 字段）
                        matched = True
                        name_counter[name] += 1
                        finding['field'] = 'name'
                        finding['name'] = name
                        finding['name_match'] = True

                    elif name_re.search(name):
                        matched = True
                        name_counter[name] += 1
                        finding['field'] = 'name'
                        finding['name'] = name
                        finding['name_match'] = True

                # 检测 Text 字段
                if check_text and not matched:
                    if '\\p1' in d['text']:
                        continue

                    visible = strip_ass_tags(d['text'])
                    if not visible.strip():
                        continue

                    matches = lang_def['_pattern_re'].findall(visible)
                    if matches:
                        for m in matches:
                            char_counter[m] += 1
                        finding['field'] = 'text'
                        finding['visible'] = visible
                        finding['matches'] = matches
                        finding['match_count'] = len(matches)
                        matched = True

                if matched:
                    findings.append(finding)
                    file_has_findings = True

            if file_has_findings:
                affected_files.add(fname)

        results[lang_key] = {
            'name': lang_def['name'],
            'description': lang_def['description'],
            'findings': findings,
            'summary': {
                'total_findings': len(findings),
                'affected_files': len(affected_files),
                'affected_file_list': sorted(affected_files),
                'top_matches': dict(char_counter.most_common(30)) if char_counter else {},
                'name_values': dict(name_counter.most_common(50)) if name_counter else {},
            },
        }

    total = sum(r['summary']['total_findings'] for r in results.values())

    return {
        'by_language': results,
        'total_findings': total,
        'mode': mode,
    }


def main():
    parser = argparse.ArgumentParser(
        description='残留源语言字符检测（多语言版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
内建语言预设: en (英语), jp (日语假名), ru (俄语西里尔), cjk (中日韩汉字)

示例:
  # 多语言检测
  python source_char_detect.py --target-dir ./target/ --langs en,jp,ru

  # 仅检测 Name 字段
  python source_char_detect.py --target-dir ./target/ --langs en,jp,ru --mode names

  # 单语言（向后兼容旧版 config）
  python source_char_detect.py --target-dir ./target/ --config source_config.json
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--config', default=None, help='JSON 配置文件（向后兼容）')
    parser.add_argument('--langs', default=None,
                        help='逗号分隔的语言代码，如 en,jp,ru（优先级高于 --config）')
    parser.add_argument('--mode', default='all', choices=['text', 'names', 'all'],
                        help='检测范围: text（仅文本）, names（仅Name字段）, all（默认）')
    args = parser.parse_args()

    config = {}
    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            config = json.load(f)

    if args.langs:
        config['languages'] = [s.strip() for s in args.langs.split(',')]

    result = detect(args.target_dir, config, mode=args.mode)

    # 打印摘要到 stderr
    for lang_key, lang_data in result['by_language'].items():
        s = lang_data['summary']
        print(f"  [{lang_data['name']}] {s['total_findings']} 处残留，"
              f"涉及 {s['affected_files']} 个文件", file=sys.stderr)

    print(f"\n总计 {result['total_findings']} 处残留（模式: {result['mode']}）", file=sys.stderr)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
