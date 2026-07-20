#!/usr/bin/env python3
"""脚本: Comment 行外语残留检测。

扫描 ASS Comment 行中的非目标语言文本和 Name 字段。
所有 Dialogue 检测脚本（source_char_detect 等）只扫描 Dialogue: 行，
Comment: 行中的外语编辑注释、角色名、语气词等会被遗漏。

用法:
  # 检测所有非 CJK 语言残留
  python comment_detect.py --target-dir <DIR> --langs en,jp,ru

  # 仅检测俄语西里尔字母
  python comment_detect.py --target-dir <DIR> --langs ru

  # 输出到文件
  python comment_detect.py --target-dir <DIR> --langs en,jp,ru > comment_findings.json

输出: JSON 到 stdout，按文件分组
"""

import argparse
import json
import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import strip_ass_tags, read_ass_file, iter_ass_files

# ═══════════════════════════════════════════════════════════════
# 语言预设（复用 source_char_detect 的预设，本地定义避免导入依赖）
# ═══════════════════════════════════════════════════════════════

LANGUAGE_PRESETS = {
    'en': {
        'name': 'English',
        'text_pattern': r'[A-Za-z]{2,}',
        'single_char_pattern': r'[A-Za-z]',
    },
    'jp': {
        'name': 'Japanese Kana',
        'text_pattern': r'[぀-ゟ゠-ヿ･-ﾟ]{1,}',
        'single_char_pattern': r'[぀-ゟ゠-ヿ･-ﾟ]',
    },
    'ru': {
        'name': 'Russian Cyrillic',
        'text_pattern': r'[А-Яа-яЁё]{1,}',
        'single_char_pattern': r'[А-Яа-яЁё]',
    },
    'cjk': {
        'name': 'CJK Ideographs',
        'text_pattern': r'[一-鿿]{1,}',
        'single_char_pattern': r'[一-鿿]',
    },
}

# 合法的非对话 Comment（保留，不标记为外语残留）
# 例如：分隔注释、空行标记、"Dialogue: 0" 包裹等
BENIGN_COMMENT_PATTERNS = [
    re.compile(r'^Dialogue:\s*0,'),   # Dialogue 被错误注释但内容是 0
]


def parse_comment_line(line: str):
    """解析 ASS Comment 行。

    ASS 格式: Comment: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    """
    if not line.startswith('Comment:'):
        return None
    parts = line.strip().split(',', 9)
    if len(parts) < 10:
        # 可能是空 Comment 或格式不完整
        return {
            'layer': '0',
            'start': '',
            'end': '',
            'style': '',
            'name': '',
            'text': '',
            'is_empty': True,
        }
    return {
        'layer': parts[0].split(': ', 1)[1] if ': ' in parts[0] else '0',
        'start': parts[1],
        'end': parts[2],
        'style': parts[3],
        'name': parts[4],
        'margin_l': parts[5],
        'margin_r': parts[6],
        'margin_v': parts[7],
        'effect': parts[8],
        'text': parts[9],
        'is_empty': False,
    }


def detect_comments(target_dir: str, lang_keys: list[str], target_lang: str = 'cjk') -> dict:
    """扫描所有 Comment 行，检测外语残留。

    Args:
        target_dir: ASS 字幕目录
        lang_keys: 要检测的外语代码列表，如 ['ru', 'en', 'jp']
        target_lang: 目标语言代码，默认为 'cjk'（中文）

    Returns:
        {
            "findings": [...],
            "summary": {...}
        }
    """
    # 构建检测器
    detectors = {}
    for key in lang_keys:
        if key in LANGUAGE_PRESETS:
            preset = LANGUAGE_PRESETS[key]
            detectors[key] = {
                'name': preset['name'],
                'text_re': re.compile(preset['text_pattern']),
                'single_re': re.compile(preset['single_char_pattern']),
            }

    if not detectors:
        return {'findings': [], 'summary': {'total': 0, 'by_file': {}, 'by_language': {}}}

    # 目标语言检测器（用于判断是否是"纯外语"行）
    target_detector = None
    if target_lang in LANGUAGE_PRESETS:
        target_detector = re.compile(LANGUAGE_PRESETS[target_lang]['single_char_pattern'])

    findings = []
    file_stats = {}   # {filename: count}
    lang_stats = {}   # {lang_key: count}
    empty_comments = []

    for fname, fpath in iter_ass_files(target_dir):
        if not fname.lower().endswith('.ass'):
            continue  # Comment 检测仅限 ASS

        lines = read_ass_file(fpath)
        file_findings = []

        for line_idx, line in enumerate(lines):
            c = parse_comment_line(line)
            if c is None:
                continue

            # 完全空的 Comment（无 style/name/text）→ 记录但不标记
            if c['is_empty']:
                if line.strip() == 'Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,,':
                    empty_comments.append({'file': fname, 'line': line_idx + 1})
                continue

            # 检查 Comment text
            text = c['text'].strip()
            name = c['name'].strip()

            text_matches = {}
            name_matches = {}

            for lang_key, det in detectors.items():
                # Text 检测
                if text:
                    text_visible = strip_ass_tags(text)
                    if det['text_re'].search(text_visible):
                        text_matches[lang_key] = {
                            'visible': text_visible[:200],
                            'lang_name': det['name'],
                        }

                # Name 检测
                if name:
                    if det['single_re'].search(name):
                        name_matches[lang_key] = {
                            'name': name,
                            'lang_name': det['name'],
                        }

            if text_matches or name_matches:
                finding = {
                    'file': fname,
                    'line': line_idx + 1,
                    'timecode': c['start'] if c['start'] else '',
                    'raw_line': line.strip()[:300],
                }
                if text_matches:
                    finding['text_matches'] = text_matches
                if name_matches:
                    finding['name_matches'] = name_matches

                # 判断整行是否可安全删除
                # 规则：Comment 行中如有非目标语言文本，整行应删除
                has_target = False
                if target_detector:
                    all_text = text + name
                    has_target = bool(target_detector.search(all_text))

                # 如果完全没有目标语言文本 → 纯外语 Comment → 直接删除
                if not has_target and (text or name):
                    finding['action'] = 'delete'
                    finding['reason'] = '纯外语Comment行，无目标语言文本'
                elif text_matches:
                    finding['action'] = 'delete'
                    finding['reason'] = 'Comment外语文本残留'
                elif name_matches:
                    finding['action'] = 'delete'
                    finding['reason'] = 'Comment Name字段外语残留'

                findings.append(finding)
                file_findings.append(finding)

                # 统计
                for lk in set(list(text_matches.keys()) + list(name_matches.keys())):
                    lang_stats[lk] = lang_stats.get(lk, 0) + 1

        if file_findings:
            file_stats[fname] = len(file_findings)

    return {
        'findings': findings,
        'summary': {
            'total_findings': len(findings),
            'affected_files': len(file_stats),
            'affected_file_list': sorted(file_stats.keys()),
            'by_language': {LANGUAGE_PRESETS[k]['name']: v for k, v in lang_stats.items()},
            'by_file': file_stats,
            'empty_comment_count': len(empty_comments),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description='Comment 行外语残留检测',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
内建语言预设: en (英语), jp (日语假名), ru (俄语西里尔)

示例:
  # 多语言检测
  python comment_detect.py --target-dir ./target/ --langs en,jp,ru

  # 仅俄语
  python comment_detect.py --target-dir ./target/ --langs ru
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标 ASS 字幕目录')
    parser.add_argument('--langs', default='en,jp,ru',
                        help='逗号分隔的语言代码（默认: en,jp,ru）')
    parser.add_argument('--target-lang', default='cjk',
                        help='目标语言代码（默认: cjk，即中文）')
    args = parser.parse_args()

    lang_keys = [s.strip() for s in args.langs.split(',')]

    result = detect_comments(args.target_dir, lang_keys, target_lang=args.target_lang)

    # 打印摘要到 stderr
    s = result['summary']
    print(f"Comment 外语残留: {s['total_findings']} 处，涉及 {s['affected_files']} 个文件", file=sys.stderr)
    if s['by_language']:
        for lang_name, count in s['by_language'].items():
            print(f"  [{lang_name}]: {count} 处", file=sys.stderr)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
