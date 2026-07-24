#!/usr/bin/env python3
"""Unified subtitle I/O — canonical read/write for SRT and ASS.

This module is the SINGLE entry point for all subtitle file reading/writing.
Every other module should go through:

    cues = read_subtitles(path)        → list[dict]
    write_subtitles(path, cues)        → None
    apply_fixes_to_cues(cues, fixes)   → int (mutates cues in-place)

All internal helpers are prefixed with _.
Public utility functions (timecode conversion, tag stripping) remain accessible.

Backward compat: srt_utils.py and whisper_utils.py I/O functions delegate here.
"""

import os
import re
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# Encoding detection
# ═══════════════════════════════════════════════════════════════

_ENCODING_CHAIN = ['utf-8-sig', 'utf-8', 'cp1251', 'koi8-r', 'shift-jis', 'gbk']


def _detect_encoding(raw_bytes: bytes) -> str:
    for enc in _ENCODING_CHAIN:
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'utf-8'


def _read_raw_lines(path: str) -> list[str]:
    with open(path, 'rb') as f:
        raw = f.read()
    encoding = _detect_encoding(raw)
    return raw.decode(encoding).splitlines(True)


def _write_raw_lines(path: str, lines: list[str]):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.writelines(lines)


# ═══════════════════════════════════════════════════════════════
# Timecode conversion
# ═══════════════════════════════════════════════════════════════

def time_to_ms(t: str) -> int:
    """SRT timecode (HH:MM:SS,mmm) → milliseconds."""
    t = t.strip()
    if ',' in t:
        time_part, ms_part = t.split(',')
    else:
        time_part, ms_part = t.split('.')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part.ljust(3, '0')[:3])
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def ms_to_time(ms: int) -> str:
    """Milliseconds → SRT timecode (HH:MM:SS,mmm)."""
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def to_seconds(tc: str) -> float:
    """Timecode string → float seconds. Handles HH:MM:SS,mmm and HH:MM:SS.mmm."""
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def format_tc(seconds: float) -> str:
    """Float seconds → SRT timecode 'HH:MM:SS,mmm'."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')


# ═══════════════════════════════════════════════════════════════
# Tag handling
# ═══════════════════════════════════════════════════════════════

SRT_TAG_RE = re.compile(r'<[^>]*>')


def strip_srt_tags(text: str) -> str:
    """Remove SRT HTML tags (<i>, <b>, <font>, etc.)."""
    return SRT_TAG_RE.sub('', text)


# ═══════════════════════════════════════════════════════════════
# SRT cue parser (internal — single cue from raw lines)
# ═══════════════════════════════════════════════════════════════

SRT_INDEX_RE = re.compile(r'^\d{1,6}\s*$')
SRT_TIMECODE_RE = re.compile(
    r'(\d{1,2}:\d{2}:\d{2}[,.]\d{2,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{2,3})'
)


def _parse_srt_cue(lines: list[str], idx: int) -> tuple[Optional[dict], int]:
    """Parse one SRT cue block from raw lines at position idx.

    Returns (cue_dict, next_idx) or (None, idx) on failure.
    cue_dict keys: start, end, text, _start_line, _srt_index
    """
    if idx >= len(lines):
        return None, idx

    # Skip blank lines
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None, idx

    start_line = idx

    # Index line
    index_line = lines[idx].strip()
    if not SRT_INDEX_RE.match(index_line):
        return None, idx
    cue_index = int(index_line)
    idx += 1

    # Timecode line
    if idx >= len(lines):
        return None, idx
    tc_match = SRT_TIMECODE_RE.match(lines[idx].strip())
    if not tc_match:
        return None, idx
    start_time = tc_match.group(1).replace(',', '.')
    end_time = tc_match.group(2).replace(',', '.')
    idx += 1

    # Text lines (may be multi-line)
    text_lines = []
    while idx < len(lines) and lines[idx].strip():
        text_lines.append(lines[idx].strip())
        idx += 1

    text = '\n'.join(text_lines) if text_lines else ''

    # Skip trailing blank lines
    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    return {
        'start': start_time,
        'end': end_time,
        'text': text,
        'start_s': to_seconds(start_time),
        'end_s': to_seconds(end_time),
        '_start_line': start_line,     # 0-based line index in raw file
        '_srt_index': cue_index,       # original SRT index number
    }, idx


def _format_srt_cue_lines(cue: dict) -> list[str]:
    """Format a cue dict back to SRT block lines (with trailing newlines)."""
    idx = cue.get('_srt_index', 1)
    start = cue['start'].replace('.', ',')
    end = cue['end'].replace('.', ',')
    text = cue['text']
    lines_out = [f"{idx}\n", f"{start} --> {end}\n"]
    for text_line in text.split('\n'):
        lines_out.append(f"{text_line}\n")
    lines_out.append('\n')
    return lines_out


# ═══════════════════════════════════════════════════════════════
# Garbled text classification
# ═══════════════════════════════════════════════════════════════

from lib.whisper_utils import OP_BOUNDARY_SEC, ED_BOUNDARY_SEC  # canonical source

KANA_RE = re.compile(r'[぀-ヿ]')
KANJI_RE = re.compile(r'[一-鿿]')
LATIN_RE = re.compile(r'[a-zA-Z]{2,}')
CYRILLIC_RE = re.compile(r'[А-Яа-яЁё]')


def classify_garbled_text(text: str, target_lang: str = 'ja') -> dict:
    """Classify subtitle text as clean or garbled.

    Returns {'type': 'clean'|'garbled', 'has_kana': bool, 'has_kanji': bool}
    """
    text = text.strip()
    if not text:
        return {'type': 'clean', 'has_kana': False, 'has_kanji': False}

    has_kana = bool(KANA_RE.search(text))
    has_kanji = bool(KANJI_RE.search(text))
    has_latin = bool(LATIN_RE.search(text))
    has_cyrillic = bool(CYRILLIC_RE.search(text))

    if target_lang == 'zh':
        if not has_latin and not has_cyrillic and not has_kana:
            return {'type': 'clean', 'has_kana': False, 'has_kanji': has_kanji}
    elif target_lang == 'en':
        if not has_cyrillic and not has_kana and not has_kanji:
            return {'type': 'clean', 'has_kana': False, 'has_kanji': has_kanji}
    else:  # ja
        if not has_latin and not has_cyrillic:
            return {'type': 'clean', 'has_kana': has_kana, 'has_kanji': has_kanji}

    return {'type': 'garbled', 'has_kana': has_kana, 'has_kanji': has_kanji}


def meaningful_char_count(text: str, target_lang: str = 'ja') -> int:
    """Count meaningful characters in the target language."""
    text = text.strip()
    if not text:
        return 0
    if target_lang == 'zh':
        return len(KANJI_RE.findall(text))
    elif target_lang == 'en':
        return len(LATIN_RE.findall(text))
    else:  # ja
        return len(KANA_RE.findall(text)) + len(KANJI_RE.findall(text))


# ═══════════════════════════════════════════════════════════════
# Public API — read / write / apply fixes
# ═══════════════════════════════════════════════════════════════

def read_subtitles(path: str, mark_garbled: bool = True,
                   target_lang: str = 'ja') -> list[dict]:
    """Parse subtitle file (SRT or ASS) into cue list.

    Each cue dict:
        start, end          — timecode strings (HH:MM:SS.mmm)
        start_s, end_s      — float seconds
        text                — subtitle text (tags stripped)
        _start_line         — 0-based line index in file
        _srt_index          — original SRT index number (1-based)
        is_garbled          — bool (only when mark_garbled=True)
        garbled_type        — 'clean' | 'garbled' (only when mark_garbled=True)

    If mark_garbled=True, OP/ED regions are exempt from garbled detection.
    """
    if path.lower().endswith('.ass'):
        cues = _read_ass_cues(path, mark_garbled=mark_garbled,
                              target_lang=target_lang)
    else:
        cues = _read_srt_cues(path, mark_garbled=mark_garbled,
                              target_lang=target_lang)
    return cues


def write_subtitles(path: str, cues: list[dict]):
    """Write cue list back to subtitle file. Auto-detects SRT vs ASS.

    Cues are re-numbered sequentially (1, 2, 3...) for SRT.
    Start/end timecodes are normalized to SRT comma format.
    """
    if path.lower().endswith('.ass'):
        return _write_ass_cues(path, cues)
    else:
        return _write_srt_cues(path, cues)


def apply_fixes_to_cues(cues: list[dict], fixes: list[dict]) -> int:
    """Apply a list of fixes to cue dicts IN MEMORY (no file I/O).

    Each fix dict:
        action: 'replace_text' | 'delete_line' | 'replace_global' | 'replace_global_regex' | 'merge_cues'
        For 'replace_text' / 'delete_line' / 'merge_cues':
            start — timecode string to locate the cue (primary key)
            line  — fallback line number if start not found
        For 'replace_global':
            original    — text to find
            replacement — text to replace with
        For 'replace_global_regex':
            pattern     — regex pattern
            replacement — replacement text
        For 'merge_cues':
            start — timecode of first cue to merge
            count — number of consecutive cues to merge

    Returns count of applied fixes.
    Caller is responsible for write_subtitles() afterward.
    """
    applied = 0

    for fix in fixes:
        action = fix.get('action', '')

        if action == 'replace_global':
            old = fix.get('original', '')
            new = fix.get('replacement', '')
            if old:
                for cue in cues:
                    if old in cue['text']:
                        cue['text'] = cue['text'].replace(old, new)
                        applied += 1

        elif action == 'replace_global_regex':
            pat = fix.get('pattern', '')
            repl = fix.get('replacement', '')
            if pat:
                regex = re.compile(pat)
                for cue in cues:
                    new_text, n = regex.subn(repl, cue['text'])
                    if n > 0:
                        cue['text'] = new_text
                        applied += n

        elif action in ('replace_text', 'delete_line', 'merge_cues'):
            # Locate cue by start timecode (primary) or line number (fallback)
            target = _find_cue(cues, fix)
            if target is None:
                continue

            if action == 'replace_text':
                target['text'] = fix.get('replacement', '')
                applied += 1

            elif action == 'delete_line':
                cues.remove(target)
                applied += 1

            elif action == 'merge_cues':
                count = fix.get('count', 2)
                idx = cues.index(target)
                merged_text = target['text']
                to_remove = []
                for i in range(1, count):
                    if idx + i < len(cues):
                        merged_text += ' ' + cues[idx + i]['text']
                        to_remove.append(cues[idx + i])
                target['text'] = merged_text
                target['end'] = to_remove[-1]['end'] if to_remove else target['end']
                target['end_s'] = to_remove[-1]['end_s'] if to_remove else target['end_s']
                for c in to_remove:
                    cues.remove(c)
                applied += 1

    return applied


def _find_cue(cues: list[dict], fix: dict) -> Optional[dict]:
    """Find cue by 'start' timecode (primary) or 'line' number (fallback)."""
    # Primary: match by start timecode
    target_start = fix.get('start', '')
    if target_start:
        tc = target_start.replace(',', '.').replace('。', '.')
        for cue in cues:
            ct = cue.get('start', '').replace(',', '.').replace('。', '.')
            if ct == tc:
                return cue

    # Fallback: match by line number (1-based)
    line_num = fix.get('line', 0)
    if line_num:
        line_idx = line_num - 1  # 0-based
        for cue in cues:
            sl = cue.get('_start_line', -1)
            if sl <= line_idx:
                # Check if line_idx is within this cue's block
                # SRT blocks are typically 3-4 lines (index + timecode + 1-2 text + blank)
                if line_idx - sl <= 4:
                    return cue

    return None


# ═══════════════════════════════════════════════════════════════
# Internal — SRT read/write
# ═══════════════════════════════════════════════════════════════

def _read_srt_cues(path: str, mark_garbled: bool = True,
                    target_lang: str = 'ja') -> list[dict]:
    """Parse SRT file into cue list."""
    lines = _read_raw_lines(path)
    cues = []
    idx = 0
    while idx < len(lines):
        cue, idx = _parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        if mark_garbled:
            classification = classify_garbled_text(cue['text'], target_lang=target_lang)
            cue['is_garbled'] = (classification['type'] == 'garbled')
            cue['garbled_type'] = classification['type']
        else:
            cue['is_garbled'] = False
            cue['garbled_type'] = 'clean'
        cues.append(cue)

    # OP/ED exemption
    if mark_garbled and cues:
        max_end_s = max(c['end_s'] for c in cues)
        ed_start = max(0, max_end_s - ED_BOUNDARY_SEC)
        for c in cues:
            if c.get('is_garbled') and (
                c['start_s'] < OP_BOUNDARY_SEC or c['start_s'] > ed_start
            ):
                c['is_garbled'] = False
                c['garbled_type'] = 'clean'

    return cues


def _write_srt_cues(path: str, cues: list[dict]):
    """Write cue list to SRT file. Cues are re-numbered sequentially."""
    lines = []
    for i, cue in enumerate(cues, 1):
        cue['_srt_index'] = i
        start = cue['start'].replace('.', ',')
        end = cue['end'].replace('.', ',')
        lines.append(f"{i}\n")
        lines.append(f"{start} --> {end}\n")
        for text_line in cue['text'].split('\n'):
            lines.append(f"{text_line}\n")
        lines.append('\n')
    _write_raw_lines(path, lines)


# ═══════════════════════════════════════════════════════════════
# Internal — ASS read/write (delegates to ass_utils)
# ═══════════════════════════════════════════════════════════════

def _read_ass_cues(path: str, mark_garbled: bool = True,
                    target_lang: str = 'ja') -> list[dict]:
    """Parse ASS file into cue list (same format as SRT cues)."""
    from lib.ass_utils import read_ass_file, parse_dialogue

    lines = read_ass_file(path)
    cues = []
    for i, line in enumerate(lines):
        d = parse_dialogue(line)
        if d is None:
            continue
        cue = {
            'start': d['start'],
            'end': d['end'],
            'text': strip_srt_tags(d['text']),
            'start_s': to_seconds(d['start']),
            'end_s': to_seconds(d['end']),
            '_start_line': i,
            '_srt_index': len(cues) + 1,
            '_ass_dialogue': d,  # preserve raw ASS fields for write-back
        }
        if mark_garbled:
            classification = classify_garbled_text(cue['text'], target_lang=target_lang)
            cue['is_garbled'] = (classification['type'] == 'garbled')
            cue['garbled_type'] = classification['type']
        else:
            cue['is_garbled'] = False
            cue['garbled_type'] = 'clean'
        cues.append(cue)
    return cues


def _write_ass_cues(path: str, cues: list[dict]):
    """Write cue list to ASS file."""
    from lib.ass_utils import read_ass_file, build_dialogue_line

    lines = read_ass_file(path)
    # Build index: line_number → cue
    cue_by_line = {}
    for cue in cues:
        sl = cue.get('_start_line', -1)
        if sl >= 0:
            cue_by_line[sl] = cue

    # Replace dialogue lines with updated cues
    for i in range(len(lines)):
        if i in cue_by_line:
            cue = cue_by_line[i]
            ass_d = cue.get('_ass_dialogue')
            if ass_d:
                ass_d['text'] = cue['text']
                lines[i] = build_dialogue_line(ass_d) + '\n'

    _write_raw_lines(path, lines)


# ═══════════════════════════════════════════════════════════════
# File iteration
# ═══════════════════════════════════════════════════════════════

def iter_subtitle_files(target_dir: str):
    """Yield (filename, full_path) for all .srt/.ass files in target_dir."""
    if not os.path.isdir(target_dir):
        return
    for fname in sorted(os.listdir(target_dir)):
        if fname.lower().endswith(('.srt', '.ass')):
            yield fname, os.path.join(target_dir, fname)


# ═══════════════════════════════════════════════════════════════
# CJK detection
# ═══════════════════════════════════════════════════════════════

CJK_RE = re.compile(r'[一-鿿]')


def contains_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    return bool(CJK_RE.search(text))
