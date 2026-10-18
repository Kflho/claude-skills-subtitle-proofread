#!/usr/bin/env python3
"""Turn cue alignments into repair proposals (P2).

Design rules:

* **diagnose first, repair second** — this module never decides *what* the
  audio says; it only consumes ``CueAlignment`` from lib.alignment.
* **no silent deletion** — ``no_speech``/``extra_text``/``missing_asr`` are
  review candidates.  Deleting a cue is a human/AI decision, not a mapper
  side effect.
* **time-only by default** — a matched cue whose boundaries drifted gets
  new timecodes.  Replacing text requires ``AlignmentConfig(
  allow_text_replace=True)`` and even then only for garbled-but-plausible
  cues.
* **neighbour-safe** — proposed time fixes are solved jointly: dense cues may
  move onto a neighbour's *old* window when both are moving, but a fix that
  would land on a cue staying put (or on another moving cue's proposal) is
  downgraded to review.

The module depends on ``ASRResult``/``CueAlignment`` only; it does not
import Qwen3 or Whisper (ADR-006 follow-up).

Usage:
    from lib.alignment import map_cues
    from fix.align_repair import plan_repairs, apply_repairs

    alignments = map_cues(cues, asr_result)
    plan = plan_repairs(cues, alignments)
    new_cues, stats = apply_repairs(cues, plan)
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI,
    ACTION_SPLIT_CANDIDATE, AlignmentConfig, CueAlignment, STATUS_MATCH,
)
from lib.asr.base import AlignResult, Token
from lib.subtitle_io import format_tc


@dataclass
class PlannedRepair:
    """One proposed change to one cue (``auto`` = safe to apply)."""

    cue_index: int
    action: str
    auto: bool = False
    cue_start: float = 0.0
    cue_end: float = 0.0
    new_start: float | None = None
    new_end: float | None = None
    old_text: str = ''
    new_text: str = ''
    confidence: str = ''
    similarity: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def delta_start(self) -> float | None:
        return None if self.new_start is None else self.new_start - self.cue_start

    @property
    def delta_end(self) -> float | None:
        return None if self.new_end is None else self.new_end - self.cue_end

    def to_dict(self) -> dict:
        out = {
            'cue_index': self.cue_index,
            'action': self.action,
            'auto': self.auto,
            'cue_start': round(self.cue_start, 3),
            'cue_end': round(self.cue_end, 3),
            'confidence': self.confidence,
            'similarity': round(self.similarity, 3),
            'reasons': list(self.reasons),
        }
        if self.new_start is not None:
            out['new_start'] = round(self.new_start, 3)
            out['new_end'] = round(self.new_end, 3)
            out['delta_start'] = round(self.delta_start or 0.0, 3)
            out['delta_end'] = round(self.delta_end or 0.0, 3)
        if self.new_text:
            out['old_text'] = self.old_text
            out['new_text'] = self.new_text
        return out

    @classmethod
    def from_dict(cls, data: dict) -> 'PlannedRepair':
        return cls(
            cue_index=int(data.get('cue_index', 0)),
            action=str(data.get('action', '')),
            auto=bool(data.get('auto', False)),
            cue_start=float(data.get('cue_start', 0.0)),
            cue_end=float(data.get('cue_end', 0.0)),
            new_start=(None if data.get('new_start') is None
                       else float(data['new_start'])),
            new_end=(None if data.get('new_end') is None
                     else float(data['new_end'])),
            old_text=str(data.get('old_text', '')),
            new_text=str(data.get('new_text', '')),
            confidence=str(data.get('confidence', '')),
            similarity=float(data.get('similarity', 0.0)),
            reasons=list(data.get('reasons', [])),
        )


# ═══════════════════════════════════════════════════════════════
# Planning
# ═══════════════════════════════════════════════════════════════

def plan_repairs(cues, alignments, *,
                 config: AlignmentConfig | None = None) -> list[PlannedRepair]:
    """Build the per-cue repair list (in cue order).

    Time fixes are planned as a set: a cue may take over a neighbour's *old*
    window when that neighbour is moving too, as long as the two proposed
    windows do not overlap.  Cues that stay put are still protected.
    """
    cfg = config or AlignmentConfig()
    by_index = {al.cue_index: al for al in alignments}
    plan: list[PlannedRepair] = []
    candidates: dict[int, tuple[float, float]] = {}

    for index, cue in enumerate(cues):
        al = by_index.get(index)
        if al is None:
            continue
        action = al.action or ACTION_KEEP
        if action == ACTION_KEEP:
            continue

        start = float(cue.get('start_s', 0.0) or 0.0)
        end = float(cue.get('end_s', start) or start)
        entry = PlannedRepair(
            cue_index=index,
            action=action,
            cue_start=start,
            cue_end=end,
            old_text=str(cue.get('text', '') or ''),
            confidence=al.confidence,
            similarity=al.similarity,
            reasons=list(al.reasons),
        )

        if action == ACTION_FIX_TIME:
            _prepare_time_fix(entry, al, cfg, candidates)
        elif action == ACTION_REPLACE_TEXT:
            entry.new_text = al.asr_text
            entry.auto = bool(cfg.allow_text_replace and al.asr_text)
            if not cfg.allow_text_replace:
                entry.reasons.append('text replacement disabled '
                                     '(allow_text_replace=False)')
        elif action == ACTION_DELETE_CANDIDATE:
            entry.reasons.append('deletion is never automatic')
        elif action == ACTION_SPLIT_CANDIDATE:
            entry.reasons.append('split proposal only; no automatic cut')

        plan.append(entry)

    _resolve_joint_time_fixes(cues, plan, candidates)
    return plan


def _prepare_time_fix(entry: PlannedRepair, al: CueAlignment,
                      cfg: AlignmentConfig,
                      candidates: dict[int, tuple[float, float]]) -> None:
    """Validate one time fix and record it as a candidate (not yet auto)."""
    if al.start is None or al.end is None:
        entry.action = ACTION_REVIEW_AI
        entry.reasons.append('alignment has no matched times')
        return

    shift = max(abs(al.start - entry.cue_start), abs(al.end - entry.cue_end))
    if shift > cfg.max_time_shift:
        entry.action = ACTION_REVIEW_AI
        entry.reasons.append(f'time shift {shift:.2f}s exceeds '
                             f'max_time_shift={cfg.max_time_shift:.2f}s')
        return

    new_start, new_end = float(al.start), float(al.end)
    if al.end_is_display_linger(cfg):
        # The cue's end is reading time, not speech: keep it instead of
        # truncating the subtitle to the ASR speech end.
        new_end = entry.cue_end
        entry.reasons.append('end kept: subtitle display linger')
    if new_end - new_start < cfg.min_cue_duration:
        new_end = new_start + cfg.min_cue_duration

    entry.new_start = new_start
    entry.new_end = new_end
    candidates[entry.cue_index] = (new_start, new_end)


def _cue_window(cue) -> tuple[float, float]:
    start = float(cue.get('start_s', 0.0) or 0.0)
    return start, float(cue.get('end_s', start) or start)


def _windows_overlap(a: tuple[float, float],
                     b: tuple[float, float]) -> bool:
    return (min(a[1], b[1]) - max(a[0], b[0])) > 1e-3


def _resolve_joint_time_fixes(cues, plan: list[PlannedRepair],
                              candidates: dict[int, tuple[float, float]]) -> None:
    """Approve time fixes in one pass; downgrade the ones still colliding.

    Cues scheduled to move are compared using their *proposed* windows, so
    dense subtitles can be re-timed together.  Any cue pair whose proposed
    windows overlap while their original windows did not is sent to review,
    and the pass is repeated — a rejected cue falls back to its old window,
    which can expose a new collision for its neighbour.  Pre-existing overlaps
    (signs, simultaneous speakers) are tolerated as before.
    """
    if not candidates:
        return

    entries = {e.cue_index: e for e in plan}
    old = [_cue_window(cue) for cue in cues]
    active = set(candidates)

    while active:
        dropped: set[int] = set()
        conflict_with: dict[int, int] = {}

        for index in sorted(active):
            if index in dropped:
                continue
            for other in range(len(cues)):
                if other == index:
                    continue
                other_window = (candidates[other]
                                if other in active and other not in dropped
                                else old[other])
                if not _windows_overlap(candidates[index], other_window):
                    continue
                if _windows_overlap(old[index], old[other]):
                    continue
                dropped.add(index)
                conflict_with[index] = other
                if other in active:
                    dropped.add(other)
                    conflict_with.setdefault(other, index)
                break

        if not dropped:
            break

        for index in sorted(dropped):
            if index not in active:
                continue
            active.discard(index)
            entry = entries[index]
            entry.action = ACTION_REVIEW_AI
            entry.auto = False
            entry.new_start = None
            entry.new_end = None
            partner = conflict_with.get(index)
            entry.reasons.append(
                f'new window overlaps cue #{partner}' if partner is not None
                else 'new window overlaps a neighbouring cue')

    for index in active:
        entries[index].auto = True


# ═══════════════════════════════════════════════════════════════
# Global offset
# ═══════════════════════════════════════════════════════════════

def estimate_global_offset(alignments, *, min_matches: int = 20,
                           min_offset: float = 0.3,
                           max_mad: float = 0.15) -> float | None:
    """Detect a consistent whole-file offset (different cut / frame rate).

    Only strong evidence is accepted: many high-similarity matched cues and
    a tight distribution (median absolute deviation).  A partial or song-only
    offset must not move the whole file.

    Returns seconds such that ``aligned = cue + offset``, or None.
    """
    import statistics

    deltas = [al.start - al.cue_start for al in alignments
              if al.status == STATUS_MATCH and al.similarity >= 0.9
              and al.start is not None]
    if len(deltas) < min_matches:
        return None
    median = statistics.median(deltas)
    mad = statistics.median(abs(d - median) for d in deltas)
    if abs(median) < min_offset or mad > max_mad:
        return None
    return median


def shift_cues(cues, delta: float) -> list[dict]:
    """Return a copy of ``cues`` shifted by ``delta`` seconds (0-clamped)."""
    shifted = []
    for cue in cues:
        item = dict(cue)
        start = float(item.get('start_s', 0.0) or 0.0) + delta
        end = float(item.get('end_s', start) or start) + delta
        if start < 0.0:
            end -= start
            start = 0.0
        item['start_s'] = round(start, 3)
        item['end_s'] = round(max(start, end), 3)
        item['start'] = format_tc(item['start_s'])
        item['end'] = format_tc(item['end_s'])
        shifted.append(item)
    return shifted


# ═══════════════════════════════════════════════════════════════
# Applying
# ═══════════════════════════════════════════════════════════════

def apply_repairs(cues, plan, *,
                  config: AlignmentConfig | None = None):
    """Apply auto-approved proposals to a copy of ``cues``.

    Returns ``(new_cues, stats)``; the input list is never mutated.  Text
    replacement is still gated on ``config.allow_text_replace`` even if a
    plan entry says ``auto=True`` — a stale plan must not bypass the flag.
    """
    cfg = config or AlignmentConfig()
    out = [dict(cue) for cue in cues]
    stats = {'fix_time': 0, 'replace_text': 0, 'skipped': 0,
             'manual_review': 0}

    for entry in plan:
        if not entry.auto:
            stats['skipped'] += 1
            stats['manual_review'] += 1
            continue
        if entry.cue_index < 0 or entry.cue_index >= len(out):
            stats['skipped'] += 1
            continue
        cue = out[entry.cue_index]

        if entry.action == ACTION_FIX_TIME:
            if entry.new_start is None or entry.new_end is None:
                stats['skipped'] += 1
                continue
            cue['start_s'] = round(entry.new_start, 3)
            cue['end_s'] = round(entry.new_end, 3)
            cue['start'] = format_tc(entry.new_start)
            cue['end'] = format_tc(entry.new_end)
            stats['fix_time'] += 1

        elif entry.action == ACTION_REPLACE_TEXT:
            if not (cfg.allow_text_replace and entry.new_text):
                stats['skipped'] += 1
                continue
            cue['text'] = entry.new_text
            stats['replace_text'] += 1

        else:
            stats['skipped'] += 1

    return out, stats


def summarize_plan(plan: list[PlannedRepair]) -> dict:
    """Counts for the run report / CLI output."""
    out: dict[str, int] = {}
    auto = 0
    for entry in plan:
        out[entry.action] = out.get(entry.action, 0) + 1
        auto += int(entry.auto)
    return {'total': len(plan), 'auto': auto, 'actions': out,
            'review': len(plan) - auto}


# ═══════════════════════════════════════════════════════════════
# Optional: re-align one cue's audio window (Step 6 of §6.4)
# ═══════════════════════════════════════════════════════════════

def realign_cue_window(audio_path: str, text: str, start: float, end: float,
                       *, aligner, language: str = 'ja',
                       pad: float = 0.15,
                       workdir: str | None = None) -> AlignResult:
    """Re-align ``text`` against [start-pad, end+pad] of ``audio_path``.

    Used after a text replacement or a split: the new text's character
    times must come from the audio, not from the old cue's boundaries.
    Tokens are returned in absolute episode time.
    """
    if end <= start:
        raise ValueError(f'invalid cue window: {start}..{end}')
    clip_start = max(0.0, float(start) - pad)
    clip_end = float(end) + pad
    duration = max(0.05, clip_end - clip_start)

    handle, wav_path = tempfile.mkstemp(suffix='.wav', dir=workdir or None)
    os.close(handle)
    try:
        from lib.subprocess_utils import run_ffmpeg
        run_ffmpeg([
            '-y', '-ss', f'{clip_start:.3f}', '-t', f'{duration:.3f}',
            '-i', audio_path, '-vn', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', wav_path,
        ])
        result = aligner.align(wav_path, text, language=language)
        shifted = [
            Token(text=t.text, start=t.start + clip_start,
                  end=t.end + clip_start, speaker=t.speaker, score=t.score)
            for t in result.tokens
        ]
        return AlignResult(
            text=result.text, language=result.language, tokens=shifted,
            meta=dict(result.meta or {}, window_start=round(clip_start, 3),
                      window_end=round(clip_end, 3)),
        )
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
