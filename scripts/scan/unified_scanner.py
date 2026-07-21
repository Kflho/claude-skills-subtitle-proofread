#!/usr/bin/env python3
"""统一字符扫描器 — 单次遍历检测所有源语言字符残留。

替代 4 个重复扫描脚本：
  - bilingual_detect.py   (双语混合 + 纯源语言)
  - source_lang_detect.py (纯源语言行)
  - source_char_detect.py (多语言字符残留)
  - issue_tracker.py      (Whisper 问题清单)

核心设计：
  1. 每个文件只读取一次
  2. 每个 cue 调用 classify_garbled_text() 统一分类
  3. 一次输出三样东西：findings JSON + per-episode issues + delete candidates

用法:
  python unified_scanner.py --target-dir <DIR> --output-findings findings.json

  同时生成 issues 和 delete candidates:
  python unified_scanner.py --target-dir <DIR> \\
    --output-findings findings.json \\
    --output-issues issues/ \\
    --output-delete delete_candidates.json

  项目感知（跳过不适用的分类）:
  python unified_scanner.py --target-dir <DIR> --project-lang ja --format srt
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

import lib._path  # noqa: F401

from lib.ass_utils import (
    strip_ass_tags, iter_ass_files, iter_dialogue_lines,
    read_ass_file, contains_cjk,
)
from lib.whisper_utils import classify_garbled_text, to_seconds, extract_ep_number, setup_windows_utf8, OP_BOUNDARY_SEC, ED_BOUNDARY_SEC


# ═══════════════════════════════════════════════════════════════
# 分类标签 → 人类可读描述
# ═══════════════════════════════════════════════════════════════

TYPE_LABELS = {
    'clean':   '纯目标语言，无外文字符',
    'garbled': '含外文字符 → VAD + Whisper',
}

LANG_LABELS = {
    'ja': '纯日语',
    'zh': '纯中文',
}


# ═══════════════════════════════════════════════════════════════
# 核心扫描逻辑
# ═══════════════════════════════════════════════════════════════

def get_oped_boundaries(cues):
    """计算 OP/ED 时间边界。开头 95s、结尾 120s 内的 cue 视为 OP/ED 区域。"""
    if not cues:
        return OP_BOUNDARY_SEC, 0
    max_end_s = max(c['end_s'] for c in cues)
    return OP_BOUNDARY_SEC, max(0, max_end_s - ED_BOUNDARY_SEC)


# ═══════════════════════════════════════════════════════════════
# 卡死重复检测（合并自 repeat_detect.py）
# ═══════════════════════════════════════════════════════════════

# 排除：scat 拟声 / 动物叫声 / 情绪表达
_EXCLUDED_SEQS = {
    'pa', 'la', 'me', 'ta',
    '汪', '喵', '哞', '咩', '咯', '咕', '嘎', '呱', '吱', '啾',
    '嗷', '呜', '哼', '嘶', '喔', '啊', '哦', '嗯', '呃',
    'woof', 'meow', 'moo', 'baa', 'quack', 'oink', 'cluck',
    'chirp', 'buzz', 'ribbit', 'neigh', 'roar', 'howl',
    'arf', 'bow', 'caw', 'coo', 'hoot', 'tweet',
    'wo', 'me', 'mu', 'ba', 'ha', 'he', 'ho', 'hi', 'hu',
}


def _is_excluded_repeat(seq, full_match):
    """综合判断是否应排除此重复序列。"""
    seq_lower = seq.lower()
    if seq_lower in _EXCLUDED_SEQS:
        return True
    # 整段仅 1-2 种字符 → 可能是情绪表达
    stripped = re.sub(r'[!！?？\s\-~～]+', '', full_match)
    if stripped:
        unique = set(stripped.lower())
        if len(unique) <= 2:
            return True
    return False


def _find_repeats(text, min_repeats=8):
    """在文本中查找 2-4 字符序列的连续重复。"""
    results = []
    for seq_len in [2, 3, 4]:
        if len(text) < seq_len * min_repeats:
            continue
        i = 0
        while i <= len(text) - seq_len * min_repeats:
            seq = text[i:i + seq_len]
            if re.search(r'[\s\-~～!！?？,，.。、；;：:]', seq):
                i += 1
                continue
            count = 1
            j = i + seq_len
            while j + seq_len <= len(text) and text[j:j + seq_len] == seq:
                count += 1
                j += seq_len
            if count >= min_repeats:
                full = text[i:j]
                if not _is_excluded_repeat(seq, full):
                    results.append({
                        'repeat_seq': seq,
                        'repeat_count': count,
                        'full_match': full,
                    })
                i = j
            else:
                i += 1
    return results


def scan_file(filepath, skip_oped=True, target_lang='ja'):
    """扫描单个字幕文件，返回该文件的分类结果。

    Args:
        filepath: SRT/ASS 文件路径
        skip_oped: 是否跳过 OP/ED 区域（开头 95s、结尾 120s）
        target_lang: 目标语言 'ja'|'zh'，影响乱码判断

    Returns:
        dict: {
            'filename': str,
            'garbled_cues': [finding, ...],
            'issues': [issue, ...],
        }
    """
    fname = os.path.basename(filepath)
    lines = read_ass_file(filepath)
    cues = []

    # 第一遍：收集所有 cue（带时间戳）
    for line_idx, d in iter_dialogue_lines(lines):
        # 跳过绘图指令行
        if '\\p1' in d.get('text', ''):
            continue
        visible = strip_ass_tags(d['text'])
        cues.append({
            'line': line_idx + 1,  # 1-based
            'start': d['start'],
            'end': d['end'],
            'text': visible,
            'raw_text': d['text'],
        })

    # 计算 OP/ED 边界
    op_boundary, ed_boundary = (95, 0)
    if cues:
        cues_with_seconds = []
        for c in cues:
            try:
                s = to_seconds(c['start'])
                e = to_seconds(c['end'])
                cues_with_seconds.append({**c, 'start_s': s, 'end_s': e})
            except (ValueError, IndexError):
                continue
        cues = cues_with_seconds
        if skip_oped:
            op_boundary, ed_boundary = get_oped_boundaries(cues)
    else:
        return {
            'filename': fname,
            'findings': {},
            'issues': [],
            'delete_candidates': [],
            'total_cues': 0,
        }

    # 第二遍：分类每个 cue + 重复检测 + 术语收集
    garbled_cues = []
    issues = []
    repeats = []
    term_freq = defaultdict(int)  # {word: count} for glossary building

    for c in cues:
        classification = classify_garbled_text(c['text'], target_lang=target_lang)
        gtype = classification['type']

        # ── 术语收集（所有 cue） ──
        text = c['text']
        if target_lang == 'ja':
            # Katakana words: 2-6 chars
            for m in re.finditer(r'[゠-ヿ]{2,6}', text):
                term_freq[m.group()] += 1
        # Kanji/hanzi compounds: 2-4 consecutive characters (shared for ja and zh)
        for m in re.finditer(r'[一-鿿]{2,4}', text):
            term_freq[m.group()] += 1

        # ── 重复检测（所有 cue） ──
        cue_repeats = _find_repeats(c['text'])
        for r in cue_repeats:
            repeats.append({
                'file': fname,
                'line': c['line'],
                'timecode': c['start'],
                'repeat_seq': r['repeat_seq'],
                'repeat_count': r['repeat_count'],
                'full_match': r['full_match'],
            })

        if gtype == 'clean':
            continue

        # OP/ED 区域豁免
        if skip_oped and (c['start_s'] < op_boundary or c['start_s'] > ed_boundary):
            continue

        # 提取集号
        ep = extract_ep_number(fname)

        # 构建 finding
        finding = {
            'file': fname,
            'line': c['line'],
            'timecode': c['start'],
            'text': c['text'][:120],
            'has_kana': classification['has_kana'],
            'has_kanji': classification['has_kanji'],
        }
        garbled_cues.append(finding)

        # 所有 garbled cue 都是 Whisper issue
        issues.append({
            'ep': ep,
            'start': c['start'],
            'end': c['end'],
            'original_text': c['text'][:120],
            'line': c['line'],
        })

    return {
        'filename': fname,
        'garbled_cues': garbled_cues,
        'issues': issues,
        'repeats': repeats,
        'term_freq': dict(term_freq),
        'total_cues': len(cues),
    }


def scan_all(target_dir, skip_oped=True, target_lang='ja'):
    """扫描目录中所有字幕文件。

    Args:
        target_lang: 目标语言 'ja'|'zh'

    Returns:
        dict: {
            'garbled_cues': [finding, ...],      # 所有文件汇总
            'per_episode_issues': {ep: [issue, ...]},
            'summary': {...},
        }
    """
    all_garbled = []
    all_issues = defaultdict(list)
    all_repeats = []
    all_terms = defaultdict(int)
    total_cues = 0
    files_scanned = 0

    for fname, fpath in iter_ass_files(target_dir):
        result = scan_file(fpath, skip_oped=skip_oped, target_lang=target_lang)
        files_scanned += 1
        total_cues += result['total_cues']

        all_garbled.extend(result['garbled_cues'])
        all_repeats.extend(result.get('repeats', []))

        for word, count in result.get('term_freq', {}).items():
            all_terms[word] += count

        for issue in result['issues']:
            all_issues[issue['ep']].append(issue)

    # 构建摘要 (v4.0)
    summary = {
        'files_scanned': files_scanned,
        'total_cues': total_cues,
        'garbled_count': len(all_garbled),
        'repeat_count': len(all_repeats),
        'term_count': len(all_terms),
        'episodes_with_issues': len(all_issues),
    }

    return {
        'garbled_cues': all_garbled,
        'per_episode_issues': dict(all_issues),
        'repeats': all_repeats,
        'term_frequencies': dict(all_terms),
        'summary': summary,
    }


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 输出函数
# ═══════════════════════════════════════════════════════════════

def write_findings_json(output, path):
    """将扫描结果写入 findings JSON。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def write_issues(output, issues_dir):
    """将 per-episode issues 写入独立 JSON 文件。"""
    os.makedirs(issues_dir, exist_ok=True)
    for ep, issues in output['per_episode_issues'].items():
        # 跳过无法识别集号的条目
        if ep == '???' or not ep.startswith('EP'):
            print(f'  跳过无效集号: {ep} ({len(issues)} issues)', file=sys.stderr)
            continue
        path = os.path.join(issues_dir, f'issues_{ep}.json')
        data = {
            'episode': ep,
            'issue_count': len(issues),
            'issues': issues,
            'source': 'unified_scanner.py',
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    setup_windows_utf8()
    parser = argparse.ArgumentParser(
        description='统一字符扫描器 — 单次遍历检测所有源语言字符残留',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础扫描
  python unified_scanner.py --target-dir ./AI审查后/ --output-findings findings.json

  # 完整输出（findings + per-episode issues）
  python unified_scanner.py --target-dir ./AI审查后/ \\
    --output-findings findings.json \\
    --output-issues issues/

  # 保留 OP/ED 区域（不跳过）
  python unified_scanner.py --target-dir ./AI审查后/ --no-skip-oped
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--output-findings', help='Findings JSON 输出路径')
    parser.add_argument('--output-issues', help='Per-episode issues 输出目录')
    parser.add_argument('--no-skip-oped', action='store_true',
                        help='不跳过 OP/ED 区域（默认跳过开头 95s + 结尾 120s）')
    parser.add_argument('--build-glossary', action='store_true',
                        help='扫描完成后自动生成术语表 proper-nouns.md')
    parser.add_argument('--glossary-output', default=None,
                        help='术语表输出路径（默认: reports/proper-nouns.md）')
    parser.add_argument('--ai-nouns', default=None,
                        help='AI WebSearch 补充的专名 JSON 路径')
    parser.add_argument('--project-lang', default='ja',
                        help='目标语言代码（ja=日语，zh=中文）。影响乱码判断逻辑。')
    parser.add_argument('--format', choices=['srt', 'ass', 'auto'], default='auto',
                        help='字幕格式（默认自动检测）')
    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f'错误: 目录不存在: {args.target_dir}', file=sys.stderr)
        sys.exit(1)

    # 扫描
    print(f'扫描目录: {args.target_dir}', file=sys.stderr)
    result = scan_all(args.target_dir, skip_oped=not args.no_skip_oped, target_lang=args.project_lang)
    s = result['summary']

    # 摘要输出到 stderr
    print(f'\n=== 扫描完成 ===', file=sys.stderr)
    print(f'文件: {s["files_scanned"]} | Cues: {s["total_cues"]} | '
          f'Garbled: {s["garbled_count"]} | Repeats: {s.get("repeat_count", 0)}',
          file=sys.stderr)
    print(file=sys.stderr)

    if s['episodes_with_issues']:
        print(f'需 Whisper 的集数: {s["episodes_with_issues"]}', file=sys.stderr)

    # 写入输出文件
    if args.output_findings:
        write_findings_json(result, args.output_findings)
        print(f'\n→ findings: {args.output_findings}', file=sys.stderr)

    if args.output_issues:
        write_issues(result, args.output_issues)
        count = len(result['per_episode_issues'])
        print(f'→ issues: {args.output_issues}/ ({count} 集)', file=sys.stderr)

    # 如果没有指定任何输出，则输出 findings 到 stdout
    if not any([args.output_findings, args.output_issues]):
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')

    # 自动生成术语表
    if args.build_glossary and args.output_findings:
        # Default: reports/proper-nouns.md (from CWD, not temp/scans/)
        glossary_out = args.glossary_output or os.path.join(
            os.getcwd(), 'reports', 'proper-nouns.md')
        print(f'\n→ 生成术语表: {glossary_out}', file=sys.stderr)
        import subprocess
        build_script = os.path.join(lib._path.SCRIPTS_DIR, 'nouns', 'build_glossary.py')
        cmd = [
            sys.executable, build_script,
            '--findings', args.output_findings,
            '--output', glossary_out,
            '--lang', args.project_lang,
        ]
        if getattr(args, 'ai_nouns', None) and os.path.exists(args.ai_nouns):
            cmd.extend(['--ai-nouns', args.ai_nouns])
        subprocess.run(cmd, check=False)
        print(f'→ 术语表完成', file=sys.stderr)


if __name__ == '__main__':
    main()
