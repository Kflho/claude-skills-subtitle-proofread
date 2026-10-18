#!/usr/bin/env python3
"""Cue-alignment contracts shared by the P2 alignment/repair layer.

Design rules (see docs/adr/007-cue-alignment.md):

* stdlib only — the alignment core must not import torch, qwen-asr, or any
  model.  That is what keeps the main process fast and testable.
* the only input is ``ASRResult`` (or a ``CharTimeline`` built from it), so
  Whisper, Qwen3, VibeVoice all feed the same mapper.
* every JSON payload carries ``schema_version`` so cached alignments can be
  invalidated instead of silently misread.

Usage:
    from lib.alignment.base import CueAlignment, AlignmentConfig

    al = CueAlignment(cue_index=0, status='match', similarity=1.0)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

ALIGNMENT_VERSION = 1

# ── cue status (additive to tech-route-2026 §6.3) ──────────────────
STATUS_MATCH = 'match'              # 文本与音频一致
STATUS_GARBLED = 'garbled'          # 文本有错，但音频有对应
STATUS_MISSING_ASR = 'missing_asr'  # cue 有文本，音频无对应（多译/幻觉）
STATUS_EXTRA_TEXT = 'extra_text'    # cue 文本比音频多（部分幻觉）
STATUS_NO_SPEECH = 'no_speech'      # cue 时间段无语音
STATUS_EMPTY_TEXT = 'empty_text'    # cue 无文本（纯图形/空行）
STATUS_NO_ASR = 'no_asr'            # 没有任何 ASR 时间轴可用

STATUSES = (
    STATUS_MATCH, STATUS_GARBLED, STATUS_MISSING_ASR, STATUS_EXTRA_TEXT,
    STATUS_NO_SPEECH, STATUS_EMPTY_TEXT, STATUS_NO_ASR,
)

# ── suggested actions (consumed by fix/align_repair.py) ────────────
ACTION_KEEP = 'keep'
ACTION_FIX_TIME = 'fix_time'
ACTION_REPLACE_TEXT = 'replace_text'
ACTION_REVIEW_AI = 'review_ai'
ACTION_REVIEW_HUMAN = 'review_human'
ACTION_DELETE_CANDIDATE = 'delete_candidate'
ACTION_SPLIT_CANDIDATE = 'split_candidate'

ACTIONS = (
    ACTION_KEEP, ACTION_FIX_TIME, ACTION_REPLACE_TEXT, ACTION_REVIEW_AI,
    ACTION_REVIEW_HUMAN, ACTION_DELETE_CANDIDATE, ACTION_SPLIT_CANDIDATE,
)


class AlignmentError(RuntimeError):
    """Raised when alignment cannot run (bad input, missing audio, ...)."""


# ═══════════════════════════════════════════════════════════════
# Text normalisation — the single source of truth for matching
# ═══════════════════════════════════════════════════════════════

# SRT HTML tags plus ASS override blocks: {\fad(200,200)}, {\pos(..)}
_TAG_RE = re.compile(r'<[^>]*>|\{[^}]*\}')
# Speaker/label prefixes fansubs put in the text: 【ナレーション】, (Sally)
_LABEL_RE = re.compile(r'【[^】]{0,20}】')


def strip_tags(text: str) -> str:
    """Remove SRT/ASS markup without touching the dialogue itself."""
    if not text:
        return ''
    return _TAG_RE.sub('', text)


def normalize_text(text: str, *, keep_latin_case: bool = False) -> str:
    """Normalise cue/ASR text into the form used for character matching.

    Steps: strip markup → NFKC → drop whitespace/punctuation/control →
    optional Latin case folding.  The result is only used for alignment;
    original text is never rewritten from it.
    """
    if not text:
        return ''
    text = _TAG_RE.sub('', text)
    text = _LABEL_RE.sub('', text)
    text = unicodedata.normalize('NFKC', text)

    out: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        # P*: punctuation, C*: control/format, Z*: separators
        if cat[0] in ('P', 'C', 'Z'):
            continue
        out.append(ch)
    result = ''.join(out)
    return result if keep_latin_case else result.lower()


def similarity_ratio(a: str, b: str) -> float:
    """Quick character-level Dice similarity (diagnostics/tests only)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

@dataclass
class AlignmentConfig:
    """Tuning knobs for time anchoring + window matching.

    Defaults are conservative on purpose: P2 must not damage clean cues.
    Everything here can be overridden per run (``run_all --align-config``)
    without touching the algorithm.
    """

    # time anchor: how far around the cue to look for ASR chars
    search_seconds: float = 6.0
    min_window_chars: int = 24
    max_window_chars: int = 600
    # blocks closer than this (in both strings) belong to one cluster
    block_gap_chars: int = 4
    # status thresholds
    match_similarity: float = 0.85
    garbled_similarity: float = 0.5
    min_speech_coverage: float = 0.15
    # repair thresholds (seconds)
    clean_boundary: float = 0.15     # below this: never touch the cue
    time_fix_threshold: float = 0.15  # above this: propose a time fix
    max_time_shift: float = 2.0      # above this: review, do not auto-apply
    min_cue_duration: float = 0.3
    # long-cue split hints
    long_cue_seconds: float = 7.0
    split_pause_seconds: float = 0.3
    # text plausibility
    repeat_window: int = 5
    max_repeat_score: float = 0.2
    # text replacement is only a proposal unless explicitly allowed
    allow_text_replace: bool = False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ═══════════════════════════════════════════════════════════════
# Per-cue alignment result
# ═══════════════════════════════════════════════════════════════

@dataclass
class CueAlignment:
    """What the mapper learned about one cue.

    ``start``/``end`` are the times of the matched ASR characters (absolute
    seconds), *not* the repaired cue times.  ``cue_start``/``cue_end`` keep
    the original values so callers can compute the delta themselves.

    ``asr_span`` is a pair of indices into the ASR *units* (tokens for
    Qwen3, segments for Whisper).  ``char_span`` indexes the character
    timeline and is what the mapper actually works with.
    """

    cue_index: int
    status: str
    similarity: float = 0.0
    matched_chars: int = 0
    cue_chars: int = 0
    cue_start: float = 0.0
    cue_end: float = 0.0
    start: float | None = None
    end: float | None = None
    asr_span: tuple[int, int] | None = None
    char_span: tuple[int, int] | None = None
    asr_text: str = ''
    confidence: str = ''
    action: str = ''
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    schema_version: int = ALIGNMENT_VERSION

    # ── derived ──────────────────────────────────────────────

    @property
    def cue_duration(self) -> float:
        return max(0.0, self.cue_end - self.cue_start)

    @property
    def aligned_duration(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return max(0.0, self.end - self.start)

    @property
    def start_error(self) -> float | None:
        """aligned_start - cue_start; >0 means the cue starts too early."""
        return None if self.start is None else self.start - self.cue_start

    @property
    def end_error(self) -> float | None:
        """cue_end - aligned_end; >0 means the cue ends too late."""
        return None if self.end is None else self.cue_end - self.end

    @property
    def max_boundary_error(self) -> float:
        errs = [abs(e) for e in (self.start_error, self.end_error)
                if e is not None]
        return max(errs) if errs else 0.0

    def end_is_display_linger(self, cfg: 'AlignmentConfig') -> bool:
        """True when the cue end is later than speech by a small margin.

        Human subtitles stay on screen after the line ends (reading time);
        CC measured against ASR speech therefore shows a systematic positive
        end error (median 0.2–0.5 s on EP01–03, and it is the majority even
        when the start matches within 150 ms).  That is a display convention,
        not a timing error: rewriting the end to the ASR end would truncate
        the subtitle.  Only differences beyond ``max_time_shift``, a negative
        end error (speech runs past the cue), or a sub-``clean_boundary``
        difference (normal cleanup) are treated as real.

        This is a *policy* grounded in the reference distribution, not a
        tuned threshold: the tolerance is the existing auto-fix bound.
        """
        return (self.status == STATUS_MATCH
                and self.start_error is not None
                and self.end_error is not None
                and abs(self.start_error) <= cfg.max_time_shift
                and cfg.clean_boundary < self.end_error <= cfg.max_time_shift)

    @property
    def is_clean(self) -> bool:
        return self.status == STATUS_MATCH and self.confidence == 'high'

    # ── serialisation ───────────────────────────────────────

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            'schema_version': self.schema_version,
            'cue_index': self.cue_index,
            'status': self.status,
            'similarity': round(self.similarity, 4),
            'matched_chars': self.matched_chars,
            'cue_chars': self.cue_chars,
            'cue_start': round(self.cue_start, 3),
            'cue_end': round(self.cue_end, 3),
            'confidence': self.confidence,
            'action': self.action,
            'reasons': list(self.reasons),
            'signals': dict(self.signals),
        }
        if self.start is not None:
            out['start'] = round(self.start, 3)
        if self.end is not None:
            out['end'] = round(self.end, 3)
        if self.asr_span is not None:
            out['asr_span'] = list(self.asr_span)
        if self.char_span is not None:
            out['char_span'] = list(self.char_span)
        if self.asr_text:
            out['asr_text'] = self.asr_text
        return out

    @classmethod
    def from_dict(cls, data: dict) -> 'CueAlignment':
        span = data.get('asr_span')
        char_span = data.get('char_span')
        return cls(
            cue_index=int(data.get('cue_index', 0)),
            status=str(data.get('status', STATUS_NO_ASR)),
            similarity=float(data.get('similarity', 0.0)),
            matched_chars=int(data.get('matched_chars', 0)),
            cue_chars=int(data.get('cue_chars', 0)),
            cue_start=float(data.get('cue_start', 0.0)),
            cue_end=float(data.get('cue_end', 0.0)),
            start=None if data.get('start') is None else float(data['start']),
            end=None if data.get('end') is None else float(data['end']),
            asr_span=None if span is None else (int(span[0]), int(span[1])),
            char_span=(None if char_span is None
                       else (int(char_span[0]), int(char_span[1]))),
            asr_text=str(data.get('asr_text', '')),
            confidence=str(data.get('confidence', '')),
            action=str(data.get('action', '')),
            reasons=list(data.get('reasons', [])),
            signals=dict(data.get('signals', {})),
            schema_version=int(data.get('schema_version', ALIGNMENT_VERSION)),
        )


@dataclass
class AlignmentReport:
    """Batch result for one episode (cheap summary for the run report)."""

    alignments: list[CueAlignment] = field(default_factory=list)
    engine: str = ''
    model: str = ''
    audio_duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # health of the timestamp source the mapper actually used
    timeline_source: str = ''
    timeline_fallback: str = ''
    timeline_quality: dict[str, Any] = field(default_factory=dict)
    schema_version: int = ALIGNMENT_VERSION

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for al in self.alignments:
            out[al.status] = out.get(al.status, 0) + 1
        return out

    def actions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for al in self.alignments:
            out[al.action] = out.get(al.action, 0) + 1
        return out

    def summary(self) -> dict:
        clean = sum(1 for a in self.alignments if a.is_clean)
        total = len(self.alignments)
        return {
            'cues': total,
            'clean': clean,
            'clean_ratio': round(clean / total, 3) if total else 0.0,
            'status': self.counts(),
            'actions': self.actions(),
        }

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'engine': self.engine,
            'model': self.model,
            'audio_duration': round(self.audio_duration, 3),
            'timeline': {
                'source': self.timeline_source,
                'fallback': self.timeline_fallback,
                'quality': dict(self.timeline_quality),
            },
            'summary': self.summary(),
            'warnings': list(self.warnings),
            'cues': [a.to_dict() for a in self.alignments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AlignmentReport':
        timeline = data.get('timeline') or {}
        return cls(
            alignments=[CueAlignment.from_dict(c)
                        for c in data.get('cues', [])],
            engine=str(data.get('engine', '')),
            model=str(data.get('model', '')),
            audio_duration=float(data.get('audio_duration', 0.0)),
            warnings=list(data.get('warnings', [])),
            timeline_source=str(timeline.get('source', '')),
            timeline_fallback=str(timeline.get('fallback', '')),
            timeline_quality=dict(timeline.get('quality') or {}),
            schema_version=int(data.get('schema_version', ALIGNMENT_VERSION)),
        )
