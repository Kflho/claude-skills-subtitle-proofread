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
* **neighbour-safe** — a proposed time fix that would overlap the previous
  or next cue is downgraded to review instead of applied.

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
    """Build the per-cue repair list (in cue order)."""
    cfg = config or AlignmentConfig()
    by_index = {al.cue_index: al for al in alignments}
    plan: list[PlannedRepair] = []

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
            _plan_time_fix(entry, cues, index, al, cfg)
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

    return plan


def _plan_time_fix(entry: PlannedRepair, cues, index: int,
                   al: CueAlignment, cfg: AlignmentConfig) -> None:
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

    new_start, new_end = al.start, al.end
    if new_end - new_start < cfg.min_cue_duration:
        new_end = new_start + cfg.min_cue_duration

    # A time fix must not land on top of a cue that was previously clear.
    # Pre-existing overlaps (signs, simultaneous speakers) are tolerated.
    conflict = _overlapping_cue(cues, index, new_start, new_end,
                                entry.cue_start, entry.cue_end)
    if conflict is not None:
        entry.action = ACTION_REVIEW_AI
        entry.reasons.append(f'new window overlaps cue #{conflict}')
        return

    entry.new_start = new_start
    entry.new_end = new_end
    entry.auto = True


def _overlapping_cue(cues, index: int, new_start: float, new_end: float,
                     old_start: float, old_end: float) -> int | None:
    """Index of a previously non-overlapping cue the fix would collide with."""
    for i, other in enumerate(cues):
        if i == index:
            continue
        other_start = float(other.get('start_s', 0.0) or 0.0)
        other_end = float(other.get('end_s', other_start) or other_start)
        touches_new = (other_end > new_start + 1e-3
                       and other_start < new_end - 1e-3)
        if not touches_new:
            continue
        overlapped_before = (other_end > old_start + 1e-3
                             and other_start < old_end - 1e-3)
        if not overlapped_before:
            return i
    return None


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
