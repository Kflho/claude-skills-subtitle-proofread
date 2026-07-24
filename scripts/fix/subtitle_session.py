#!/usr/bin/env python3
"""Subtitle session — path resolution, detection, and cache management.

Provides a single session object that WhisperFixer, FragmentProcessor,
and the Fixer orchestrator compose.  Owns all path resolution, SRT file
finding, video detection, cue loading, and VAD/review-cluster cache
persistence.

No I/O beyond temp caches (VAD, review clusters) and SRT reading
for detection.  Does NOT write SRT or the problem report.
"""

import json
import os
import re
import sys

import lib._path  # noqa: F401 — ensure scripts/ on sys.path

from lib.whisper_utils import (
    setup_windows_utf8, parse_srt, to_seconds, format_tc,
)
setup_windows_utf8()

# ── Whisper defaults (read from environment) ──

DEFAULT_WHISPER_CLI = os.environ.get('WHISPER_CLI', '')
DEFAULT_WHISPER_MODEL = os.environ.get('WHISPER_MODEL', '')
DEFAULT_RETRY_MODEL = os.environ.get('WHISPER_RETRY_MODEL', '')


class SubtitleSession:
    """Episode path resolution + detection + cache management.

    Public attributes (set by _resolve_paths):
        episode: str
        project_dir: str
        target_lang: str
        srt_path: str | None
        srt_name: str | None
        video_path: str | None
        video_dir: str | None   — resolved video directory
        episode_title: str | None
        ref_srt_path: str | None
        whisper_cli: str
        model: str | None
        retry_model: str | None
    """

    def __init__(self, episode: str, project_dir: str, *,
                 target_lang: str = 'ja',
                 video_dir: str = None,
                 whisper_cli: str = None,
                 model: str = None,
                 retry_model: str = None,
                 srt_dir: str = None):
        self.episode = episode
        self.project_dir = project_dir
        self.target_lang = target_lang

        # Resolved paths (populated by _resolve_paths)
        self.srt_path = None
        self.srt_name = None
        self.video_path = None
        self.video_dir = None
        self.episode_title = None
        self.ref_srt_path = None

        # Whisper config
        self.whisper_cli = whisper_cli or DEFAULT_WHISPER_CLI
        self.model = model or DEFAULT_WHISPER_MODEL
        self.retry_model = retry_model or DEFAULT_RETRY_MODEL

        # User-supplied overrides
        self._srt_dir_override = srt_dir
        self._video_dir_override = video_dir

        # Lazy-loaded caches
        self._cues = None
        self._ref_cues = None

        if not self.whisper_cli:
            print('[SubtitleSession] WARNING: WHISPER_CLI env var not set. '
                  'Set it or pass whisper_cli= to use Whisper fixes.',
                  file=sys.stderr)

        self._resolve_paths()

    # ── Computed path properties ──

    @property
    def srt_dir(self) -> str:
        """Directory containing SRT/ASS files."""
        if self._srt_dir_override:
            return self._srt_dir_override
        from lib.project_utils import get_target_dir
        return get_target_dir(self.project_dir)

    @property
    def temp_dir(self) -> str:
        """temp/scans/ directory for intermediate artifacts."""
        return os.path.join(self.project_dir, 'temp', 'scans')

    @property
    def report_path(self) -> str:
        """Path to 问题解决报告.md."""
        return os.path.join(self.project_dir, 'reports', '问题解决报告.md')

    @property
    def review_dir(self) -> str:
        """Directory for manual review checklists and clips."""
        return os.path.join(self.project_dir, 'reports', 'manual-review')

    # ── Path resolution ──

    def _resolve_paths(self):
        """Find SRT/ASS and video files for this episode."""
        # Build search token
        if (self.episode.startswith('EP') and len(self.episode) > 2
                and self.episode[2:].isdigit()):
            search_token = self.episode[2:]
        else:
            search_token = self.episode

        # Find SRT/ASS
        srt_dir = self.srt_dir
        if os.path.isdir(srt_dir):
            for fname in sorted(os.listdir(srt_dir)):
                if not fname.endswith(('.srt', '.ass')):
                    continue
                stem = os.path.splitext(fname)[0]
                if search_token.isdigit():
                    if search_token in fname:
                        self.srt_path = os.path.join(srt_dir, fname)
                        self.srt_name = fname
                        break
                elif stem == search_token:
                    self.srt_path = os.path.join(srt_dir, fname)
                    self.srt_name = fname
                    break

        # Find video
        candidates = []
        if self._video_dir_override:
            candidates.append(self._video_dir_override)
        candidates.extend([
            os.path.join(self.project_dir, 'video'),
            os.path.join(self.project_dir, 'videos'),
        ])
        exts = ('.mkv', '.mp4', '.avi', '.mov')
        for vdir in candidates:
            if not os.path.isdir(vdir):
                continue
            for fname in sorted(os.listdir(vdir)):
                if search_token in fname and fname.lower().endswith(exts):
                    self.video_path = os.path.join(vdir, fname)
                    self.video_dir = vdir
                    # Extract episode title from filename
                    m = re.search(r'-\s*\d{3}\s*-\s*(.+?)\s*\(', fname)
                    if m:
                        self.episode_title = m.group(1).strip()
                    return

    # ── Detection ──

    def is_clean(self) -> bool:
        """Check if SRT has any garbled cues remaining.

        Fast scan — no audio, no Whisper.  Reads SRT, runs
        classify_garbled_text on every cue.  Returns True if
        all cues are clean target-language text.
        """
        if not self.srt_path or not os.path.exists(self.srt_path):
            return True
        cues = parse_srt(self.srt_path, mark_garbled=True,
                         target_lang=self.target_lang)
        return not any(c.get('is_garbled') for c in cues)

    def problem_count(self) -> int:
        """Count garbled cues in current SRT."""
        if not self.srt_path or not os.path.exists(self.srt_path):
            return 0
        cues = parse_srt(self.srt_path, mark_garbled=True,
                         target_lang=self.target_lang)
        return sum(1 for c in cues if c.get('is_garbled'))

    # ── Cue loading ──

    def load_cues(self):
        """Load current SRT cues into memory (lazy, cached)."""
        if self._cues is None and self.srt_path:
            self._cues = parse_srt(self.srt_path, mark_garbled=True,
                                   target_lang=self.target_lang)
        return self._cues

    # ── Missing subtitle check ──

    def has_missing_subtitles(self) -> bool:
        """Check if findings.json has missing_subtitles for this episode."""
        findings_path = os.path.join(self.temp_dir, 'findings.json')
        if not os.path.exists(findings_path):
            return False
        try:
            with open(findings_path, 'r', encoding='utf-8') as f:
                findings = json.load(f)
            gaps = findings.get('missing_subtitles', {}).get(self.episode, [])
            return len(gaps) > 0
        except Exception:
            return False

    # ── Reference subtitle context ──

    def load_ref_cues(self):
        """Load parsed reference SRT cues (lazy, cached)."""
        if self._ref_cues is not None:
            return self._ref_cues
        if not self.ref_srt_path or not os.path.exists(self.ref_srt_path):
            self._ref_cues = []
            return self._ref_cues
        try:
            self._ref_cues = parse_srt(self.ref_srt_path, mark_garbled=False)
        except Exception:
            self._ref_cues = []
        return self._ref_cues

    def find_ref_text(self, start_s: float, end_s: float,
                      max_chars: int = 200) -> str:
        """Find reference subtitle text overlapping a time range.

        Returns the text of the reference cue with the greatest time
        overlap, or empty string if none found.
        """
        ref_cues = self.load_ref_cues()
        if not ref_cues:
            return ''

        best_overlap = 0.0
        best_text = ''
        for cue in ref_cues:
            overlap = min(end_s, cue['end_s']) - max(start_s, cue['start_s'])
            if overlap > best_overlap and overlap > 0:
                best_overlap = overlap
                best_text = cue.get('text', '').strip()

        if best_text and len(best_text) > max_chars:
            best_text = best_text[:max_chars] + '…'
        return best_text

    # ── Placeholder safety net ──

    def convert_placeholders_to_markers(self) -> int:
        """Convert any remaining ⚠SPEECH placeholders in SRT to [???].

        This is a safety net — placeholders should have been handled by
        the triage, but edge cases can leave residuals.

        Returns count of converted markers.
        """
        if not self.srt_path or not os.path.exists(self.srt_path):
            return 0
        try:
            with open(self.srt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            count = content.count('⚠SPEECH')
            if count > 0:
                content = content.replace('⚠SPEECH', '[???]')
                with open(self.srt_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f'[safety] {count} ⚠SPEECH → [???] '
                      f'in {os.path.basename(self.srt_path)}',
                      file=sys.stderr)
            return count
        except Exception as e:
            print(f'[safety] Placeholder scan failed: {e}', file=sys.stderr)
            return 0

    # ── Generic lookup helpers ──

    @staticmethod
    def find_cue_index(cues: list, timecode: str) -> int | None:
        """Find cue index by start timecode (tolerant of comma/dot)."""
        tc = timecode.replace(',', '.').replace('。', '.')
        for i, cue in enumerate(cues):
            ct = cue.get('start', '').replace(',', '.').replace('。', '.')
            if ct == tc:
                return i
        return None

    @staticmethod
    def find_cluster_for_timecode(clusters: list, timecode: str) -> dict | None:
        """Find the review cluster containing a given cue timecode."""
        if not clusters:
            return None
        tc = timecode.replace(',', '.').replace('。', '.')
        for cl in clusters:
            for g in cl.get('garbled', []):
                gt = g.get('start', '').replace(',', '.').replace('。', '.')
                if gt == tc:
                    return cl
        return None

    # ── VAD cache persistence ──

    def _vad_path(self) -> str:
        """Path to per-episode VAD speech segments JSON."""
        return os.path.join(self.temp_dir, f'{self.episode}_vad.json')

    def save_speech_segs(self, speech_segs: list):
        """Persist VAD speech_segs for later use (human review / AI apply)."""
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
            with open(self._vad_path(), 'w', encoding='utf-8') as f:
                json.dump({'speech_segs': speech_segs}, f, ensure_ascii=False)
        except Exception as e:
            print(f'[vad] Failed to save speech_segs: {e}', file=sys.stderr)

    def load_speech_segs(self) -> list:
        """Load persisted VAD speech_segs, or empty list."""
        path = self._vad_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f).get('speech_segs', [])
            except Exception:
                pass
        return []

    # ── Review cluster cache persistence ──

    def _review_clusters_path(self) -> str:
        return os.path.join(self.temp_dir,
                            f'{self.episode}_review_clusters.json')

    def save_review_clusters(self, clusters: list):
        """Save cluster info as JSON for apply() to use in VAD alignment."""
        try:
            os.makedirs(self.temp_dir, exist_ok=True)
            serializable = []
            for cl in clusters:
                serializable.append({
                    'ss': cl['ss'], 'es': cl['es'],
                    'garbled': [{'start': g['start'], 'end': g['end'],
                                 'text': g['text'][:80],
                                 'start_s': g['start_s'],
                                 'end_s': g['end_s']}
                                for g in cl['garbled']],
                    'left_text': cl.get('left_text', ''),
                    'right_text': cl.get('right_text', ''),
                })
            with open(self._review_clusters_path(), 'w',
                      encoding='utf-8') as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[review] Failed to save clusters: {e}', file=sys.stderr)

    def load_review_clusters(self) -> list:
        """Load persisted review clusters."""
        path = self._review_clusters_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    # ── VAD clip boundary helpers (static utilities) ──

    @staticmethod
    def compute_review_clip_bounds(garbled_start: float, garbled_end: float,
                                   cluster_ss: float, cluster_es: float,
                                   speech_segs: list,
                                   max_pad: float = 2.0) -> tuple | None:
        """Compute video clip boundaries for human review — VAD-aware.

        Returns (clip_start_s, clip_end_s) or None if no speech detected.
        None = auto-cut (don't generate clip).
        """
        if not speech_segs:
            return (max(cluster_ss, garbled_start - 0.5),
                    min(cluster_es, garbled_end + 0.5))

        # Speech overlap with garbled cues
        garbled_speech_dur = 0.0
        for ss, es in speech_segs:
            overlap = min(es, garbled_end) - max(ss, garbled_start)
            if overlap > 0:
                garbled_speech_dur += overlap

        if garbled_speech_dur <= 0:
            return None

        # Initial: garbled ± max_pad
        clip_start = max(cluster_ss, garbled_start - max_pad)
        clip_end = min(cluster_es, garbled_end + max_pad)

        # Snap to VAD boundaries (capped)
        for ss, es in speech_segs:
            if es >= garbled_start and ss <= garbled_end:
                if ss < garbled_start and garbled_start - ss <= max_pad:
                    clip_start = min(clip_start, ss)
                if es > garbled_end and es - garbled_end <= max_pad:
                    clip_end = max(clip_end, es)
        clip_start = max(cluster_ss, clip_start)
        clip_end = min(cluster_es, clip_end)

        # Trim long silences at edges
        clip_start = SubtitleSession._trim_leading_silence(
            clip_start, clip_end, max_pad, speech_segs)
        clip_end = SubtitleSession._trim_trailing_silence(
            clip_start, clip_end, max_pad, speech_segs)

        return clip_start, clip_end

    @staticmethod
    def _trim_leading_silence(start_s: float, end_s: float, max_pad: float,
                              speech_segs: list) -> float:
        """Advance start_s if > max_pad of silence before first speech."""
        first_speech = None
        for ss, es in speech_segs:
            if es >= start_s:
                first_speech = ss
                break
        if first_speech is not None and first_speech > start_s:
            silence = first_speech - start_s
            if silence > max_pad:
                return first_speech - max_pad
        return start_s

    @staticmethod
    def _trim_trailing_silence(start_s: float, end_s: float, max_pad: float,
                               speech_segs: list) -> float:
        """Pull back end_s if > max_pad of silence after last speech."""
        last_speech = None
        for ss, es in reversed(speech_segs):
            if ss <= end_s:
                last_speech = es
                break
        if last_speech is not None and last_speech < end_s:
            silence = end_s - last_speech
            if silence > max_pad:
                return last_speech + max_pad
        return end_s
