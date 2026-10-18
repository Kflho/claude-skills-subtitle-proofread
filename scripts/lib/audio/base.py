#!/usr/bin/env python3
"""Audio-event contracts (VAD/AED/diarization hints).

Dependency-free by design: the main skill process and the FireRedVAD
worker (separate venv) share these dataclasses, so JSON round-trips are
stable.  Optional providers return an empty ``AudioHints`` when disabled
(ADR-011 null-stage rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONTRACT_VERSION = 1


class AudioProviderError(RuntimeError):
    """Raised when an audio-event provider cannot produce hints."""


@dataclass(frozen=True)
class AudioEvent:
    """One timed audio event; ``kind`` is speech / singing / music."""

    kind: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind,
            'start': round(self.start, 3),
            'end': round(self.end, 3),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AudioEvent':
        return cls(
            kind=str(data.get('kind', '')),
            start=float(data.get('start', 0.0)),
            end=float(data.get('end', 0.0)),
        )


@dataclass
class AudioHints:
    """Timeline hints for one episode.

    ``events`` is a flat list (kind + span).  Hints are advisory only:
    the pipeline must not drop cues because VAD missed them (ADR-010).
    Speaker diarization is out of scope (user decision 2026-10).
    """

    events: list[AudioEvent] = field(default_factory=list)
    source: str = ''
    duration: float = 0.0
    confidence: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def empty(cls) -> 'AudioHints':
        return cls()

    def by_kind(self, kind: str) -> list[AudioEvent]:
        return [e for e in self.events if e.kind == kind]

    def summary(self) -> dict:
        """Per-kind seconds / ratios, using ``duration`` as denominator."""
        total = float(self.duration or 0.0)
        if total <= 0:
            total = max((e.end for e in self.events), default=0.0)
        out = {}
        for kind in ('speech', 'singing', 'music'):
            events = self.by_kind(kind)
            seconds = sum(e.duration for e in events)
            out[kind] = {
                'segments': len(events),
                'seconds': round(seconds, 2),
                'ratio': round(seconds / total, 3) if total > 0 else 0.0,
            }
        out['duration'] = round(total, 2)
        return out

    def to_dict(self) -> dict:
        return {
            'contract_version': CONTRACT_VERSION,
            'source': self.source,
            'duration': round(self.duration, 3),
            'confidence': dict(self.confidence),
            'meta': dict(self.meta),
            'events': [e.to_dict() for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AudioHints':
        events = [AudioEvent.from_dict(e) for e in data.get('events', [])
                  if isinstance(e, dict)]
        events.sort(key=lambda e: (e.start, e.end, e.kind))
        return cls(
            events=events,
            source=str(data.get('source', '')),
            duration=float(data.get('duration', 0.0) or 0.0),
            confidence=dict(data.get('confidence') or {}),
            meta=dict(data.get('meta') or {}),
        )
