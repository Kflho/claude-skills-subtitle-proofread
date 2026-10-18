#!/usr/bin/env python3
"""ASR data contracts shared by all engines.

This module is intentionally dependency-free (stdlib only).  The Qwen3
worker runs in an isolated venv and imports these dataclasses; the main
skill process imports the same types, so JSON round-trips are stable.

Usage:
    from lib.asr.base import ASRResult, Segment, Token, ASRError

    result = ASRResult(text='...', language='Japanese', engine='qwen3')
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


CONTRACT_VERSION = 1


class ASRError(RuntimeError):
    """Raised when an ASR backend cannot produce a usable result."""


@dataclass(frozen=True)
class BackendInfo:
    """Static metadata for one adapter.

    ``kind`` is ``asr`` or ``aligner``.  ``requires_env`` lets the router
    decide whether an engine is safe to auto-select (e.g. Qwen3 needs a
    dedicated interpreter).  ``auto_priority`` breaks ties: higher wins.
    """

    name: str
    kind: str = 'asr'
    version: str = ''
    capabilities: tuple[str, ...] = ()
    auto_priority: int = 0
    requires_env: tuple[str, ...] = ()
    description: str = ''


@dataclass
class ASRConfig:
    """Engine-neutral configuration passed to every adapter's build().

    New engines read these fields (and ``options``) instead of adding new
    constructor parameters to the router.  This keeps the core stable.
    """

    model: str = ''
    aligner_model: str = ''
    python: str = ''
    device: str = 'cuda:0'
    dtype: str = 'bfloat16'
    request_timeout: float = 1800.0
    max_new_tokens: int = 4096
    options: dict[str, Any] = field(default_factory=dict)

    def option(self, key: str, default=None):
        return self.options.get(key, default)


@dataclass
class Token:
    """One timed token (word / character / punctuation group)."""

    text: str
    start: float
    end: float
    speaker: str | None = None
    score: float | None = None

    def to_dict(self) -> dict:
        out = {'text': self.text, 'start': round(self.start, 3),
               'end': round(self.end, 3)}
        if self.speaker is not None:
            out['speaker'] = self.speaker
        if self.score is not None:
            out['score'] = round(self.score, 4)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> 'Token':
        return cls(
            text=str(data.get('text', '')),
            start=float(data.get('start', 0.0)),
            end=float(data.get('end', 0.0)),
            speaker=data.get('speaker'),
            score=data.get('score'),
        )


@dataclass
class Segment:
    """A contiguous utterance, optionally carrying its tokens."""

    text: str
    start: float
    end: float
    tokens: list[Token] = field(default_factory=list)
    speaker: str | None = None

    def to_dict(self) -> dict:
        out = {'text': self.text, 'start': round(self.start, 3),
               'end': round(self.end, 3),
               'tokens': [t.to_dict() for t in self.tokens]}
        if self.speaker is not None:
            out['speaker'] = self.speaker
        return out

    @classmethod
    def from_dict(cls, data: dict) -> 'Segment':
        return cls(
            text=str(data.get('text', '')),
            start=float(data.get('start', 0.0)),
            end=float(data.get('end', 0.0)),
            tokens=[Token.from_dict(t) for t in data.get('tokens', [])],
            speaker=data.get('speaker'),
        )


@dataclass
class ASRResult:
    """Engine-neutral transcription result.

    Attributes:
        text: full transcript.
        language: engine-reported language (canonical name, e.g. Japanese).
        segments: utterance-level segments; may be empty for engines that
            only return tokens.
        tokens: global token timeline in absolute seconds.  This is the
            primary input for cue alignment (P2).
        engine: qwen3 | whisper | vibevoice | mai | scribe | gemini.
        model: model id/path used by the engine.
        audio_duration: source audio duration in seconds (0 if unknown).
        context_hash: short hash of the glossary/context used, so caches
            invalidate when the term list changes.
        meta: engine-specific extras (device, tier, warnings, ...).
    """

    text: str
    language: str = ''
    segments: list[Segment] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    engine: str = ''
    model: str = ''
    audio_duration: float = 0.0
    context_hash: str = ''
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CONTRACT_VERSION

    @property
    def has_timestamps(self) -> bool:
        return bool(self.tokens)

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'text': self.text,
            'language': self.language,
            'segments': [s.to_dict() for s in self.segments],
            'tokens': [t.to_dict() for t in self.tokens],
            'engine': self.engine,
            'model': self.model,
            'audio_duration': round(self.audio_duration, 3),
            'context_hash': self.context_hash,
            'meta': self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ASRResult':
        return cls(
            text=str(data.get('text', '')),
            language=str(data.get('language', '')),
            segments=[Segment.from_dict(s) for s in data.get('segments', [])],
            tokens=[Token.from_dict(t) for t in data.get('tokens', [])],
            engine=str(data.get('engine', '')),
            model=str(data.get('model', '')),
            audio_duration=float(data.get('audio_duration', 0.0)),
            context_hash=str(data.get('context_hash', '')),
            meta=dict(data.get('meta', {})),
            schema_version=int(data.get('schema_version', CONTRACT_VERSION)),
        )


@dataclass
class AlignResult:
    """Result of aligning a known text to audio (Qwen3-ForcedAligner)."""

    text: str
    language: str = ''
    tokens: list[Token] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'text': self.text,
            'language': self.language,
            'tokens': [t.to_dict() for t in self.tokens],
            'meta': self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AlignResult':
        return cls(
            text=str(data.get('text', '')),
            language=str(data.get('language', '')),
            tokens=[Token.from_dict(t) for t in data.get('tokens', [])],
            meta=dict(data.get('meta', {})),
            schema_version=int(data.get('schema_version', CONTRACT_VERSION)),
        )


@runtime_checkable
class ASRBackend(Protocol):
    """Minimal interface every ASR engine must implement."""

    name: str

    def available(self) -> bool:
        """Return True if the engine can be invoked (not a live health check)."""

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = False) -> ASRResult:
        """Transcribe one 16 kHz mono WAV file."""


@runtime_checkable
class Aligner(Protocol):
    """Minimal interface every forced aligner must implement."""

    name: str

    def align(self, audio_path: str, text: str, *,
              language: str = 'ja') -> AlignResult:
        """Align known text to one WAV file."""


def validate_backend(backend) -> list[str]:
    """Return a list of ASRBackend contract violations (empty = conforming)."""
    errors: list[str] = []
    if not getattr(backend, 'name', ''):
        errors.append('missing non-empty .name')
    available = getattr(backend, 'available', None)
    if not callable(available):
        errors.append('missing callable .available()')
    elif not isinstance(available(), bool):
        errors.append('.available() must return bool')
    if not callable(getattr(backend, 'transcribe', None)):
        errors.append('missing callable .transcribe()')
    return errors


def validate_aligner(aligner) -> list[str]:
    """Return a list of Aligner contract violations."""
    errors: list[str] = []
    if not getattr(aligner, 'name', ''):
        errors.append('missing non-empty .name')
    available = getattr(aligner, 'available', None)
    if not callable(available):
        errors.append('missing callable .available()')
    elif not isinstance(available(), bool):
        errors.append('.available() must return bool')
    if not callable(getattr(aligner, 'align', None)):
        errors.append('missing callable .align()')
    return errors


def build_segments(tokens: list[Token], *, gap_s: float = 0.8,
                   max_chars: int = 60,
                   sentence_marks: str = '。！？!?') -> list[Segment]:
    """Group a token timeline into utterance-ish segments.

    Used by the Qwen3 worker (which returns a flat timestamp list) and by
    tests.  This is deliberately simple: P2 cue alignment uses tokens, not
    these segments.
    """
    if not tokens:
        return []

    ordered = sorted(tokens, key=lambda t: (t.start, t.end))
    segments: list[Segment] = []
    cur: list[Token] = []
    cur_len = 0

    def flush():
        nonlocal cur_len
        if not cur:
            return
        text = ''.join(t.text for t in cur).strip()
        if text:
            segments.append(Segment(
                text=text,
                start=cur[0].start,
                end=cur[-1].end,
                tokens=list(cur),
                speaker=cur[0].speaker,
            ))
        cur.clear()
        cur_len = 0

    for token in ordered:
        if cur:
            prev = cur[-1]
            gap = token.start - prev.end
            too_long = cur_len >= max_chars
            sentence_end = bool(prev.text) and prev.text[-1] in sentence_marks
            if gap >= gap_s or too_long or sentence_end:
                flush()
        cur.append(token)
        cur_len += len(token.text)
    flush()
    return segments


def language_name(code: str) -> str:
    """Map ISO-ish codes to the names Qwen3 uses ('ja' -> 'Japanese')."""
    mapping = {
        'ja': 'Japanese', 'jp': 'Japanese', 'japanese': 'Japanese',
        'zh': 'Chinese', 'chinese': 'Chinese',
        'en': 'English', 'english': 'English',
        'ko': 'Korean', 'korean': 'Korean',
    }
    return mapping.get((code or '').strip().lower(), code or 'Japanese')
