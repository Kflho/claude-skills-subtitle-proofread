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
import sys
from collections import defaultdict

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from ass_utils import (
    strip_ass_tags, iter_ass_files, iter_dialogue_lines,
    read_ass_file, contains_cjk,
)
from whisper_utils import classify_garbled_text, to_seconds, extract_ep_number


# ═══════════════════════════════════════════════════════════════
# 分类标签 → 人类可读描述
# ═══════════════════════════════════════════════════════════════

TYPE_LABELS = {
    'pure_romaji':      '纯罗马字（需词典修复或 Whisper）',
    'mixed_romaji':     '罗马字+日语混合行',
    'ai_noise':         'AI 噪声碎片（可直接删除）',
    'hallucination':    '时代错位幻觉（iPhone/Google 等）',
    'cyrillic':         '西里尔字母残留',
    'music_tag':        '音乐/音效标签（非乱码）',
    'clean':            '无外文字符',
}


# ═══════════════════════════════════════════════════════════════
# 核心扫描逻辑
# ═══════════════════════════════════════════════════════════════

def get_oped_boundaries(cues):
    """计算 OP/ED 时间边界。开头 95s、结尾 120s 内的 cue 视为 OP/ED 区域。"""
    if not cues:
        return 95, 0
    max_end_s = max(c['end_s'] for c in cues)
    return 95, max_end_s - 120


def scan_file(filepath, skip_oped=True):
    """扫描单个字幕文件，返回该文件的分类结果。

    Args:
        filepath: SRT/ASS 文件路径
        skip_oped: 是否跳过 OP/ED 区域（开头 95s、结尾 120s）

    Returns:
        dict: {
            'filename': str,
            'findings': {type: [finding, ...]},
            'issues': [issue, ...],        # 供 Whisper 阶段使用
            'delete_candidates': [fix, ...] # 供 romaji_fixer 删除
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

    # 第二遍：分类每个 cue
    findings_by_type = defaultdict(list)
    issues = []
    delete_candidates = []

    for c in cues:
        classification = classify_garbled_text(c['text'])
        gtype = classification['type']

        if gtype == 'clean' or gtype == 'music_tag':
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
            'type': gtype,
            'text': c['text'][:120],
            'has_kana': classification['has_kana'],
            'has_kanji': classification['has_kanji'],
        }
        findings_by_type[gtype].append(finding)

        # 生成 Whisper issue（需要音频重转录的类型）
        if gtype in ('pure_romaji', 'mixed_romaji'):
            issues.append({
                'ep': ep,
                'start': c['start'],
                'end': c['end'],
                'original_text': c['text'][:120],
                'type': gtype,
                'line': c['line'],
            })

        # 生成删除候选（噪声碎片和幻觉词）
        if classification['is_deletable']:
            delete_candidates.append({
                'action': 'delete_line',
                'file': fname,
                'line': c['line'],
                'note': f'统一扫描: {gtype} — "{c["text"][:40]}"',
            })

    return {
        'filename': fname,
        'findings': dict(findings_by_type),
        'issues': issues,
        'delete_candidates': delete_candidates,
        'total_cues': len(cues),
    }


def scan_all(target_dir, skip_oped=True):
    """扫描目录中所有字幕文件。

    Returns:
        dict: {
            'findings': {type: [finding, ...]},  # 所有文件汇总
            'per_episode_issues': {ep: [issue, ...]},
            'delete_candidates': [fix, ...],
            'summary': {...},
        }
    """
    all_findings = defaultdict(list)
    all_issues = defaultdict(list)
    all_deletes = []
    total_cues = 0
    files_scanned = 0

    for fname, fpath in iter_ass_files(target_dir):
        result = scan_file(fpath, skip_oped=skip_oped)
        files_scanned += 1
        total_cues += result['total_cues']

        for gtype, findings in result['findings'].items():
            all_findings[gtype].extend(findings)

        for issue in result['issues']:
            all_issues[issue['ep']].append(issue)

        all_deletes.extend(result['delete_candidates'])

    # 构建摘要
    summary = {
        'files_scanned': files_scanned,
        'total_cues': total_cues,
        'total_findings': sum(len(v) for v in all_findings.values()),
        'total_issues': sum(len(v) for v in all_issues.values()),
        'total_delete_candidates': len(all_deletes),
        'by_type': {t: len(v) for t, v in all_findings.items()},
        'episodes_with_issues': len(all_issues),
        'type_labels': TYPE_LABELS,
    }

    return {
        'findings': dict(all_findings),
        'per_episode_issues': dict(all_issues),
        'delete_candidates': all_deletes,
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


def write_delete_candidates(delete_list, path):
    """将删除候选写入 JSON（兼容 apply_fixes.py 格式）。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(delete_list, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='统一字符扫描器 — 单次遍历检测所有源语言字符残留',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础扫描
  python unified_scanner.py --target-dir ./AI审查后/ --output-findings findings.json

  # 完整输出（findings + issues + delete candidates）
  python unified_scanner.py --target-dir ./AI审查后/ \\
    --output-findings findings.json \\
    --output-issues issues/ \\
    --output-delete delete_candidates.json

  # 保留 OP/ED 区域（不跳过）
  python unified_scanner.py --target-dir ./AI审查后/ --no-skip-oped
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--output-findings', help='Findings JSON 输出路径')
    parser.add_argument('--output-issues', help='Per-episode issues 输出目录')
    parser.add_argument('--output-delete', help='删除候选 JSON 输出路径')
    parser.add_argument('--no-skip-oped', action='store_true',
                        help='不跳过 OP/ED 区域（默认跳过开头 95s + 结尾 120s）')
    parser.add_argument('--project-lang', default='ja',
                        help='项目语言代码（ja=日语，zh=中文）— 保留供未来扩展')
    parser.add_argument('--format', choices=['srt', 'ass', 'auto'], default='auto',
                        help='字幕格式（默认自动检测）')
    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f'错误: 目录不存在: {args.target_dir}', file=sys.stderr)
        sys.exit(1)

    # 扫描
    print(f'扫描目录: {args.target_dir}', file=sys.stderr)
    result = scan_all(args.target_dir, skip_oped=not args.no_skip_oped)
    s = result['summary']

    # 摘要输出到 stderr
    print(f'\n=== 扫描完成 ===', file=sys.stderr)
    print(f'文件: {s["files_scanned"]} | Cues: {s["total_cues"]} | '
          f'发现: {s["total_findings"]} | Issues: {s["total_issues"]} | '
          f'可删除: {s["total_delete_candidates"]}',
          file=sys.stderr)
    print(file=sys.stderr)

    if s['by_type']:
        print('按类型分布:', file=sys.stderr)
        for gtype, count in sorted(s['by_type'].items(), key=lambda x: -x[1]):
            label = TYPE_LABELS.get(gtype, gtype)
            print(f'  {gtype}: {count} ({label})', file=sys.stderr)

    if s['episodes_with_issues']:
        print(f'\n需 Whisper 的集数: {s["episodes_with_issues"]}', file=sys.stderr)

    # 写入输出文件
    if args.output_findings:
        write_findings_json(result, args.output_findings)
        print(f'\n→ findings: {args.output_findings}', file=sys.stderr)

    if args.output_issues:
        write_issues(result, args.output_issues)
        count = len(result['per_episode_issues'])
        print(f'→ issues: {args.output_issues}/ ({count} 集)', file=sys.stderr)

    if args.output_delete:
        write_delete_candidates(result['delete_candidates'], args.output_delete)
        print(f'→ delete: {args.output_delete} ({len(result["delete_candidates"])} 条)',
              file=sys.stderr)

    # 如果没有指定任何输出，则输出 findings 到 stdout
    if not any([args.output_findings, args.output_issues, args.output_delete]):
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')


if __name__ == '__main__':
    main()
