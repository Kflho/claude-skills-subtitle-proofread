#!/usr/bin/env python3
"""Shared project-level utilities — mode/format detection, file lookup, git backup.

Used by both run_all.py (top-level orchestrator) and episode_workflow.py
(single-episode subprocess) to avoid duplicating helper functions.
"""

import json
import os
import re
import subprocess


def load_json(path):
    """Load a JSON file, return None if missing."""
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def detect_mode(project_dir):
    """Auto-detect workflow mode based on project resources.

    Returns:
        'text'  — 参考字幕/ directory has files (use reference subs for comparison)
        'audio' — no reference subs, rely on VAD + Whisper only

    Note: 原始字幕/ is a backup directory, not reference subtitles.
    Reference subtitles (official translations, human-proofread, etc.) go in 参考字幕/.
    """
    ref_dir = os.path.join(project_dir, '参考字幕')
    if os.path.isdir(ref_dir) and os.listdir(ref_dir):
        return 'text'
    return 'audio'


def detect_format(project_dir):
    """Detect subtitle format(s) in the project.

    Returns:
        dict: {
            'has_ass': bool,
            'has_srt': bool,
            'primary': 'ass' | 'srt' | 'mixed' | None,
            'ass_count': int,
            'srt_count': int,
        }
    """
    target_dir = os.path.join(project_dir, 'AI审查后')
    if not os.path.isdir(target_dir):
        return {'has_ass': False, 'has_srt': False, 'primary': None,
                'ass_count': 0, 'srt_count': 0}

    ass_count = 0
    srt_count = 0
    for fname in os.listdir(target_dir):
        lname = fname.lower()
        if lname.endswith('.ass'):
            ass_count += 1
        elif lname.endswith('.srt'):
            srt_count += 1

    if ass_count > srt_count:
        primary = 'ass'
    elif srt_count > ass_count:
        primary = 'srt'
    else:
        primary = 'mixed' if ass_count > 0 else None

    return {
        'has_ass': ass_count > 0,
        'has_srt': srt_count > 0,
        'primary': primary,
        'ass_count': ass_count,
        'srt_count': srt_count,
    }


def norm_ep(episode):
    """Normalize episode identifier: '64' → 'EP064', 'EP064' → 'EP064'."""
    episode = str(episode).strip().upper()
    if episode.startswith('EP'):
        return f'EP{int(episode[2:]):03d}'
    return f'EP{int(episode):03d}'


def find_video(project_dir, episode, video_dir=None):
    """Find video file for an episode. Returns path or None.

    Searches: explicit video_dir > project/video > project/videos.
    """
    ep_num = episode[2:]  # '064' from 'EP064'
    exts = ('.mkv', '.mp4', '.avi', '.mov')

    candidates = []
    if video_dir:
        candidates.append(video_dir)
    candidates.extend([
        os.path.join(project_dir, 'video'),
        os.path.join(project_dir, 'videos'),
    ])

    for vdir in candidates:
        if not os.path.isdir(vdir):
            continue
        for fname in os.listdir(vdir):
            if ep_num in fname and fname.lower().endswith(exts):
                return os.path.join(vdir, fname)

    return None


def find_srt(project_dir, episode):
    """Find the SRT file for an episode. Returns (filename, full_path) or (None, None)."""
    target_dir = os.path.join(project_dir, 'AI审查后')
    ep_num = episode[2:]  # '064' from 'EP064'
    for fname in os.listdir(target_dir):
        if fname.endswith('.srt') and ep_num in fname:
            return fname, os.path.join(target_dir, fname)
    return None, None


def find_original_srt(project_dir, episode):
    """Find the original backup SRT. Returns full_path or None."""
    orig_dir = os.path.join(project_dir, '原始字幕')
    if not os.path.isdir(orig_dir):
        return None
    ep_num = episode[2:]
    for fname in os.listdir(orig_dir):
        if fname.endswith('.srt') and ep_num in fname:
            return os.path.join(orig_dir, fname)
    return None


def git_backup(project_dir, message):
    """Auto git add + commit. Returns commit hash or status string."""
    try:
        subprocess.run(['git', 'add', '-A'], cwd=project_dir,
                       capture_output=True, timeout=10)
        result = subprocess.run(
            ['git', 'commit', '-m', message],
            cwd=project_dir, capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0:
            m = re.search(r'\[[\w-]+ ([a-f0-9]+)\]', result.stdout)
            return m.group(1)[:7] if m else 'ok'
        return 'ok'  # nothing to commit
    except Exception as e:
        return f'error: {e}'
