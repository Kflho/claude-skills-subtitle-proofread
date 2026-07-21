#!/usr/bin/env python3
"""脚本: 清理空字幕 cue（通用，支持 SRT/ASS）。

删除文本为空的字幕条目。常见于批量删除操作后的残留。

用法:
  python clean_empty_cues.py --target-dir <DIR> [--dry-run]
"""

import argparse
import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.whisper_utils import setup_windows_utf8
from lib.ass_utils import read_ass_file, write_ass_file, iter_ass_files
from lib import srt_utils


def clean_srt(lines: list[str]) -> tuple[list[str], int]:
    """从 SRT 行列表中移除空文本的 cue。"""
    cues = []
    idx = 0
    while idx < len(lines):
        cue, idx = srt_utils.parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        cues.append(cue)

    removed = 0
    kept_cues = []
    for cue in cues:
        if not cue['text'].strip():
            removed += 1
        else:
            kept_cues.append(cue)

    if removed == 0:
        return lines, 0

    # Rebuild lines without empty cues, renumbering indices
    new_lines = []
    for i, cue in enumerate(kept_cues, 1):
        cue['_srt_index'] = i
        new_lines.extend(srt_utils.build_srt_cue_lines(cue))

    return new_lines, removed


def clean_ass(lines: list[str]) -> tuple[list[str], int]:
    """从 ASS 行列表中移除空文本的 Dialogue 行。"""
    from lib.ass_utils import parse_dialogue
    removed = 0
    new_lines = []
    for line in lines:
        d = parse_dialogue(line)
        if d and not d['text'].strip():
            removed += 1
            continue
        new_lines.append(line)
    return new_lines, removed


def main():
    setup_windows_utf8()
    parser = argparse.ArgumentParser(description='清理空字幕 cue')
    parser.add_argument('--target-dir', required=True, help='目标字幕目录')
    parser.add_argument('--dry-run', action='store_true', help='仅预览')
    args = parser.parse_args()

    total_removed = 0
    for fname, fpath in iter_ass_files(args.target_dir):
        lines = read_ass_file(fpath)
        is_srt = fname.lower().endswith('.srt')

        if is_srt:
            new_lines, removed = clean_srt(lines)
        else:
            new_lines, removed = clean_ass(lines)

        if removed > 0:
            total_removed += removed
            print(f'  {fname}: 删除 {removed} 个空 cue')
            if not args.dry_run:
                write_ass_file(fpath, new_lines)

    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f'\n{prefix}共删除 {total_removed} 个空 cue')


if __name__ == '__main__':
    main()
