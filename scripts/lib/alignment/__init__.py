"""Cue alignment + repair layer (2026 tech route, P2).

Stable seams:
    base.py        — contracts (CueAlignment/AlignmentConfig/AlignmentReport),
                     text normalisation, statuses & actions
    timeline.py    — ASRResult → character timeline (tokens or segments)
    cue_mapper.py  — time anchor + windowed character alignment
    diagnostics.py — signals, confidence tiers, acceptance statistics
    aligner.py     — aligner service over the plugin registry

Nothing here imports torch/model code; the main process stays light and
tests stay fast.  Repair lives in ``fix/align_repair.py``.
"""

from lib.alignment.base import (
    ACTION_DELETE_CANDIDATE, ACTION_FIX_TIME, ACTION_KEEP,
    ACTION_REPLACE_TEXT, ACTION_REVIEW_AI, ACTION_REVIEW_HUMAN,
    ACTION_SPLIT_CANDIDATE, ALIGNMENT_VERSION, AlignmentConfig,
    AlignmentError, AlignmentReport, CueAlignment, STATUS_EMPTY_TEXT,
    STATUS_EXTRA_TEXT, STATUS_GARBLED, STATUS_MATCH, STATUS_MISSING_ASR,
    STATUS_NO_ASR, STATUS_NO_SPEECH, normalize_text, similarity_ratio,
    strip_tags,
)
from lib.alignment.timeline import CharTimeline, build_char_timeline
from lib.alignment.cue_mapper import map_cues, map_report
from lib.alignment.diagnostics import (
    boundary_stats, confidence_for, summarize,
)
from lib.alignment.aligner import AlignerService, build_aligner

__all__ = [
    'ACTION_DELETE_CANDIDATE', 'ACTION_FIX_TIME', 'ACTION_KEEP',
    'ACTION_REPLACE_TEXT', 'ACTION_REVIEW_AI', 'ACTION_REVIEW_HUMAN',
    'ACTION_SPLIT_CANDIDATE', 'ALIGNMENT_VERSION', 'AlignerService',
    'AlignmentConfig', 'AlignmentError', 'AlignmentReport', 'CharTimeline',
    'CueAlignment', 'STATUS_EMPTY_TEXT', 'STATUS_EXTRA_TEXT',
    'STATUS_GARBLED', 'STATUS_MATCH', 'STATUS_MISSING_ASR', 'STATUS_NO_ASR',
    'STATUS_NO_SPEECH', 'boundary_stats', 'build_aligner',
    'build_char_timeline', 'confidence_for', 'map_cues', 'map_report',
    'normalize_text', 'similarity_ratio', 'strip_tags', 'summarize',
]
