#!/usr/bin/env python3
"""脚本: OP/ED 时间码区间检测 (通用, 支持 SRT/ASS)。

扫描多集字幕文件，识别每集固定位置重复出现的 OP/ED 片段。
基于时间码跨集聚类，无需参考字幕即可检测。

用法:
  python oped_detect.py --target-dir <DIR> --min-episodes <N>

输出: JSON，包含每集的 OP/ED 起止时间码区间和建议
"""

import argparse
import json
import sys
import os
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ass_utils import read_ass_file, iter_ass_files, iter_dialogue_lines, time_to_ms, ms_to_time


def detect_oped_timecodes(target_dir: str, min_episodes: int = 10,
                          max_op_seconds: int = 180, max_ed_seconds: int = 180) -> dict:
    """通过跨集时间码聚类检测 OP/ED 区间。

    Args:
        target_dir: 字幕目录
        min_episodes: 最少出现集数阈值
        max_op_seconds: OP 最大搜索范围 (秒，从文件头开始)
        max_ed_seconds: ED 最大搜索范围 (秒，从文件尾倒数)

    Returns:
        {
            "op_candidates": [{"start_ms": N, "end_ms": N, "episode_count": N, ...}],
            "ed_candidates": [...],
            "episode_timecodes": {filename: {"op_range": [...], "ed_range": [...]}},
            "summary": {...}
        }
    """
    episode_head_cues = {}  # fname → [(start_ms, end_ms, text)]
    episode_tail_cues = {}

    for fname, fpath in iter_ass_files(target_dir):
        lines = read_ass_file(fpath)
        head_cues = []
        tail_cues = []
        max_file_ms = 0

        for _, d in iter_dialogue_lines(lines):
            start_ms = time_to_ms(d['start'])
            end_ms = time_to_ms(d['end'])
            if end_ms > max_file_ms:
                max_file_ms = end_ms

            if start_ms < max_op_seconds * 1000:
                head_cues.append((start_ms, end_ms, d['text']))

        # Collect tail cues
        tail_cutoff = max_file_ms - max_ed_seconds * 1000
        for _, d in iter_dialogue_lines(lines):
            start_ms = time_to_ms(d['start'])
            if start_ms >= tail_cutoff:
                tail_cues.append((start_ms, time_to_ms(d['end']), d['text']))

        if head_cues:
            episode_head_cues[fname] = head_cues
        if tail_cues:
            episode_tail_cues[fname] = tail_cues

    # Cluster by timecode similarity
    def cluster_timecodes(episode_cues: dict, tolerance_ms: int = 2000) -> list[dict]:
        """跨集聚类相似时间码的 cue。"""
        # Collect all (start_ms, text) across episodes
        all_starts = []
        for fname, cues in episode_cues.items():
            for start_ms, end_ms, text in cues:
                all_starts.append((start_ms, end_ms, text, fname))

        if not all_starts:
            return []

        all_starts.sort()

        clusters = []
        current_cluster = [all_starts[0]]
        current_center = all_starts[0][0]

        for i in range(1, len(all_starts)):
            start_ms = all_starts[i][0]
            if abs(start_ms - current_center) <= tolerance_ms:
                current_cluster.append(all_starts[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [all_starts[i]]
                current_center = start_ms

        clusters.append(current_cluster)

        # Filter: only keep clusters that appear in enough episodes
        result = []
        for cluster in clusters:
            episodes = set(c[3] for c in cluster)
            if len(episodes) >= min_episodes:
                starts = [c[0] for c in cluster]
                ends = [c[1] for c in cluster]
                texts = [c[2] for c in cluster]
                result.append({
                    'start_ms': min(starts),
                    'end_ms': max(ends),
                    'avg_start_ms': sum(starts) // len(starts),
                    'avg_end_ms': sum(ends) // len(ends),
                    'episode_count': len(episodes),
                    'cue_count': len(cluster),
                    'sample_texts': texts[:5],
                })

        return sorted(result, key=lambda c: c['avg_start_ms'])

    op_candidates = cluster_timecodes(episode_head_cues, tolerance_ms=3000)
    ed_candidates = cluster_timecodes(episode_tail_cues, tolerance_ms=3000)

    # Find continuous OP/ED blocks
    def find_blocks(candidates: list[dict], gap_ms: int = 10000) -> list[dict]:
        """将相邻的候选聚类合并为连续的 OP/ED 块。"""
        if not candidates:
            return []
        blocks = []
        current_block = candidates[0]
        for i in range(1, len(candidates)):
            gap = candidates[i]['avg_start_ms'] - current_block['avg_end_ms']
            if gap <= gap_ms:
                # Extend current block
                current_block['avg_end_ms'] = candidates[i]['avg_end_ms']
                current_block['end_ms'] = max(current_block['end_ms'], candidates[i]['end_ms'])
                current_block['episode_count'] = max(current_block['episode_count'], candidates[i]['episode_count'])
                current_block['cue_count'] += candidates[i]['cue_count']
            else:
                blocks.append(current_block)
                current_block = candidates[i]
        blocks.append(current_block)
        return blocks

    op_blocks = find_blocks(op_candidates)
    ed_blocks = find_blocks(ed_candidates)

    # Build per-episode OP/ED ranges
    episode_timecodes = {}
    for fname, fpath in iter_ass_files(target_dir):
        ep_data = {'op_ranges': [], 'ed_ranges': []}
        for block in op_blocks:
            ep_data['op_ranges'].append({
                'start': ms_to_time(block['avg_start_ms'], 'srt'),
                'end': ms_to_time(block['avg_end_ms'], 'srt'),
            })
        for block in ed_blocks:
            ep_data['ed_ranges'].append({
                'start': ms_to_time(block['avg_end_ms'], 'srt'),
                'end': ms_to_time(block['avg_end_ms'], 'srt'),
            })
        episode_timecodes[fname] = ep_data

    return {
        'op_blocks': [{
            'start': ms_to_time(b['avg_start_ms'], 'srt'),
            'end': ms_to_time(b['avg_end_ms'], 'srt'),
            'start_ms': b['avg_start_ms'],
            'end_ms': b['avg_end_ms'],
            'episode_count': b['episode_count'],
            'cue_count': b['cue_count'],
        } for b in op_blocks],
        'ed_blocks': [{
            'start': ms_to_time(b['avg_start_ms'], 'srt'),
            'end': ms_to_time(b['avg_end_ms'], 'srt'),
            'start_ms': b['avg_start_ms'],
            'end_ms': b['avg_end_ms'],
            'episode_count': b['episode_count'],
            'cue_count': b['cue_count'],
        } for b in ed_blocks],
        'episode_timecodes': episode_timecodes,
        'summary': {
            'total_files': len(episode_head_cues),
            'op_block_count': len(op_blocks),
            'ed_block_count': len(ed_blocks),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description='OP/ED 时间码区间检测 (通用)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python oped_detect.py --target-dir ./target/
  python oped_detect.py --target-dir ./target/ --min-episodes 50
        """
    )
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--min-episodes', type=int, default=10,
                        help='最少出现集数阈值 (默认: 10)')
    parser.add_argument('--max-op-seconds', type=int, default=180,
                        help='OP 最大搜索范围/秒 (默认: 180)')
    parser.add_argument('--max-ed-seconds', type=int, default=180,
                        help='ED 最大搜索范围/秒 (默认: 180)')
    args = parser.parse_args()

    result = detect_oped_timecodes(
        args.target_dir,
        min_episodes=args.min_episodes,
        max_op_seconds=args.max_op_seconds,
        max_ed_seconds=args.max_ed_seconds,
    )

    s = result['summary']
    print(f"检测完成: {s['total_files']} 集, "
          f"{s['op_block_count']} 个 OP 块, {s['ed_block_count']} 个 ED 块", file=sys.stderr)

    if result['op_blocks']:
        print("\nOP 候选区间:", file=sys.stderr)
        for b in result['op_blocks']:
            print(f"  [{b['start']} → {b['end']}] "
                  f"跨 {b['episode_count']} 集, {b['cue_count']} 条 cue", file=sys.stderr)

    if result['ed_blocks']:
        print("\nED 候选区间:", file=sys.stderr)
        for b in result['ed_blocks']:
            print(f"  [{b['start']} → {b['end']}] "
                  f"跨 {b['episode_count']} 集, {b['cue_count']} 条 cue", file=sys.stderr)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
