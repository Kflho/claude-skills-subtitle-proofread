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


def get_target_dir(project_dir):
    """Return the directory containing subtitle files to process.

    Resolution order:
      1. Env var SUBTITLE_INPUT_DIR (absolute or relative to project_dir)
      2. Env var INPUT_DIR (set by run_all.py --input-dir)
      3. Default: project_dir/AI审查后
    """
    env_val = os.environ.get('SUBTITLE_INPUT_DIR') or os.environ.get('INPUT_DIR')
    if env_val:
        if env_val == '.':
            return project_dir
        if os.path.isabs(env_val):
            return env_val
        return os.path.join(project_dir, env_val)
    return os.path.join(project_dir, 'AI审查后')


# ═══════════════════════════════════════════════════════════════
# Language auto-detection — replaces --lang ja/zh hard requirement
# ═══════════════════════════════════════════════════════════════

# Regex patterns for script detection (shared across all language detectors)
_CJK_RE = re.compile(r'[一-鿿]')
_KANA_RE = re.compile(r'[ぁ-ヿ]')
_CYRILLIC_RE = re.compile(r'[Ѐ-ӿ]')
_LATIN_RE = re.compile(r'[a-zA-Z]')
_MUSIC_MARKER_RE = re.compile(r'^\[[^\]]+\]$')


def _is_noise_for_lang_detect(text: str) -> bool:
    """Quick noise check for language detection sampling.

    Filters out music markers, very short text, and pure numbers/symbols
    before counting scripts. Less strict than whisper_utils._is_noise() —
    we just need clean samples for script statistics.
    """
    t = text.strip()
    if not t or len(t) < 2:
        return True
    if _MUSIC_MARKER_RE.match(t):
        return True
    # Pure numbers/symbols (no letters in any script)
    if not (_CJK_RE.search(t) or _KANA_RE.search(t) or
            _CYRILLIC_RE.search(t) or _LATIN_RE.search(t)):
        return True
    return False


def _detect_cue_script(text: str) -> str:
    """Detect the dominant script of a single cue's text.

    Returns: 'kana' (Japanese — has hiragana/katakana),
             'cjk'  (Chinese — CJK without kana),
             'cyrillic', 'latin', or 'unknown'.
    """
    t = text.strip()
    cjk = len(_CJK_RE.findall(t))
    kana = len(_KANA_RE.findall(t))
    cyrillic = len(_CYRILLIC_RE.findall(t))
    latin = len(_LATIN_RE.findall(t))

    # Prioritize: Kana > CJK > Cyrillic > Latin
    # Kana first because Japanese always has kana; CJK-only = Chinese
    if kana > max(cjk, cyrillic, latin):
        return 'kana'
    if cjk > max(kana, cyrillic, latin):
        return 'cjk'
    if cyrillic > max(cjk, kana, latin):
        return 'cyrillic'
    if latin > 0:
        return 'latin'
    return 'unknown'


def detect_project_lang(project_dir: str, sample_size: int = 100,
                        cache: bool = True) -> str:
    """Auto-detect the dominant language of subtitle files in a project.

    Samples non-noise cues from AI审查后/ SRT files and classifies each
    by its dominant script.  Language is determined by aggregate script
    distribution across the entire sample — single-script cues can't
    distinguish Chinese from Japanese, but the corpus always can.

    Detection rules (after sampling ≥sample_size non-noise cues):
      - kana-dominant corpus  → 'ja' (Japanese — always has kana mixed in)
      - cjk-dominant, no kana → 'zh' (Chinese — kanji without kana)
      - cyrillic-dominant     → 'ru'
      - latin-dominant        → 'en'

    Result is cached to temp/scans/project_lang.json for sub-scripts.
    Pass cache=False to force re-detection.

    Args:
        project_dir: project root (must contain AI审查后/ with SRT files)
        sample_size: number of non-noise cues to sample (default 100)
        cache: whether to write/read the cached result
    """
    cache_path = os.path.join(project_dir, 'temp', 'scans', 'project_lang.json')

    # ── Read cache ──
    if cache and os.path.exists(cache_path):
        try:
            cached = load_json(cache_path)
            if cached and cached.get('lang'):
                return cached['lang']
        except Exception:
            pass

    # ── Sample SRT files ──
    from lib.whisper_utils import parse_srt as _parse_srt

    target_dir = get_target_dir(project_dir)
    if not os.path.isdir(target_dir):
        return 'ja'  # Default: Japanese (most common for this tool)

    srt_files = sorted([f for f in os.listdir(target_dir)
                        if f.endswith('.srt')])
    if not srt_files:
        return 'ja'

    scripts = {'kana': 0, 'cjk': 0, 'cyrillic': 0, 'latin': 0, 'unknown': 0}
    sampled = 0

    for fname in srt_files:
        if sampled >= sample_size:
            break
        try:
            cues = list(_parse_srt(os.path.join(target_dir, fname),
                                   mark_garbled=False))
        except Exception:
            continue
        for cue in cues:
            if sampled >= sample_size:
                break
            text = cue.get('text', '')
            if _is_noise_for_lang_detect(text):
                continue
            script = _detect_cue_script(text)
            scripts[script] += 1
            sampled += 1

    if sampled == 0:
        return 'ja'

    # ── Determine language ──
    total = sum(scripts.values())
    kana_ratio = scripts['kana'] / total if total > 0 else 0
    cjk_ratio = scripts['cjk'] / total if total > 0 else 0

    # Japanese: significant kana presence (≥15% of cues have kana-dominant text)
    if kana_ratio >= 0.15:
        lang = 'ja'
    # Chinese: CJK-dominant but no kana
    elif cjk_ratio >= 0.3:
        lang = 'zh'
    # Cyrillic
    elif scripts['cyrillic'] / max(total, 1) >= 0.3:
        lang = 'ru'
    # Latin/English
    elif scripts['latin'] / max(total, 1) >= 0.5:
        lang = 'en'
    else:
        lang = 'ja'  # Default fallback

    # ── Cache ──
    if cache:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'lang': lang,
                    'sample_size': sampled,
                    'scripts': scripts,
                    'kana_ratio': round(kana_ratio, 3),
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return lang


def detect_resources(project_dir, video_dir=None):
    """Detect available resources for the proofread pipeline.

    Reads paths from environment variables (set in CLAUDE.md) and verifies
    they actually exist on disk. Returns a dict of what's available.

    Args:
        project_dir: project root directory
        video_dir: explicit video directory (from --video-dir flag)

    Returns:
        dict with keys: has_target_subs, has_video, video_dir, has_whisper,
                        has_reference, reference_dir
    """
    resources = {
        'has_target_subs': False,
        'has_video': False,
        'video_dir': None,
        'has_whisper': False,
        'has_reference': False,
        'reference_dir': None,
    }

    # ── Target subtitles (required) ──
    target_dir = get_target_dir(project_dir)
    if os.path.isdir(target_dir):
        srt_files = [f for f in os.listdir(target_dir)
                     if f.endswith(('.srt', '.ass'))]
        resources['has_target_subs'] = len(srt_files) > 0

    # ── Video files ──
    vdir = video_dir
    if not vdir or not os.path.isdir(vdir):
        for d in [os.path.join(project_dir, 'video'),
                  os.path.join(project_dir, 'videos')]:
            if os.path.isdir(d):
                vdir = d
                break
    if vdir and os.path.isdir(vdir):
        video_files = [f for f in os.listdir(vdir)
                       if f.lower().endswith(('.mkv', '.mp4', '.avi', '.mov'))]
        if video_files:
            resources['has_video'] = True
            resources['video_dir'] = vdir

    # ── Whisper backend detection (multi-backend) ──
    # Priority: 1. WHISPER_BACKEND env var  2. auto-detect from available tools
    try:
        from lib.whisper_backends import detect_available_backends as _detect_backends
        available_backends = _detect_backends()
        resources['whisper_backends'] = available_backends
        resources['whisper_backend'] = os.environ.get('WHISPER_BACKEND', '').strip() or (
            available_backends[0] if available_backends else '')
    except ImportError:
        available_backends = []
        resources['whisper_backends'] = []
        resources['whisper_backend'] = ''

    # Legacy check: WHISPER_CLI + WHISPER_MODEL (for backward compat)
    whisper_cli = os.environ.get('WHISPER_CLI', '')
    whisper_model = os.environ.get('WHISPER_MODEL', '')
    has_legacy_whisper = bool(whisper_cli and os.path.isfile(whisper_cli) and whisper_model)

    resources['has_whisper'] = bool(available_backends) or has_legacy_whisper

    # ── Reference subtitles ──
    ref_dir = os.path.join(project_dir, '参考字幕')
    if os.path.isdir(ref_dir):
        ref_files = [f for f in os.listdir(ref_dir)
                     if f.endswith(('.srt', '.ass'))]
        if ref_files:
            resources['has_reference'] = True
            resources['reference_dir'] = ref_dir

    return resources


def resources_summary(resources):
    """One-line resource status string for startup banner."""
    def _ok(b):
        return '[+]' if b else '[-]'
    backend = resources.get('whisper_backend', '')
    whisper_label = f'Whisper({backend})' if backend else 'Whisper'
    parts = [
        f"字幕{_ok(resources['has_target_subs'])}",
        f"视频{_ok(resources['has_video'])}",
        f"{whisper_label}{_ok(resources['has_whisper'])}",
        f"参考{_ok(resources['has_reference'])}",
    ]
    return 'Resources: ' + ' '.join(parts)


def can_use_whisper(resources, skip_whisper=False):
    """Single source of truth: can we run Whisper audio fix?"""
    return (not skip_whisper
            and resources.get('has_video', False)
            and resources.get('has_whisper', False))


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
    target_dir = get_target_dir(project_dir)
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
    """Find the subtitle file for an episode. Returns (filename, full_path) or (None, None)."""
    target_dir = get_target_dir(project_dir)
    if episode.startswith('EP') and len(episode) > 2 and episode[2:].isdigit():
        ep_num = episode[2:]  # '064' from 'EP064'
    else:
        ep_num = episode  # file stem for non-EP IDs
    for fname in os.listdir(target_dir):
        if fname.endswith(('.srt', '.ass')):
            stem = os.path.splitext(fname)[0]
            if ep_num.isdigit() and ep_num in fname:
                return fname, os.path.join(target_dir, fname)
            elif stem == ep_num:
                return fname, os.path.join(target_dir, fname)
    return None, None


def find_original_srt(project_dir, episode):
    """Find the original backup SRT. Returns full_path or None."""
    orig_dir = os.path.join(project_dir, '原始字幕')
    if not os.path.isdir(orig_dir):
        return None
    ep_num = episode[2:] if episode.startswith('EP') else episode
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
