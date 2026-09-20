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

# BOM 前缀 → 编码。排在最前：BOM 是文件自己声明的，比任何猜测都可靠。
# ASS 的行业惯例就是 UTF-16LE + BOM —— 少了这一步，整类文件都读不出来。
_BOMS = (
    (b'\xef\xbb\xbf', 'utf-8-sig'),
    (b'\xff\xfe\x00\x00', 'utf-32-le'),
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
    (b'\xff\xfe', 'utf-16-le'),
    (b'\xfe\xff', 'utf-16-be'),
)

# 无 BOM 时的试探顺序。**单字节编码必须排在最后**：cp1251/koi8-r/latin-1
# 对任意字节串都能解码成功，一旦排在前面，后面的 shift-jis/gbk 就成了永远
# 走不到的死代码，任何非 UTF-8 文件都会被静默解成西里尔乱码（实测：一份
# UTF-16LE 的 ASS 被判定为 koi8-r，解析出 0 条 cue 且不报错）。
_ENCODING_CHAIN = ['utf-8', 'gbk', 'big5', 'shift-jis', 'euc-kr',
                   'cp1251', 'koi8-r', 'latin-1']


def _looks_like_utf16(raw_bytes: bytes) -> str:
    """无 BOM 的 UTF-16 探测：取前若干字节，看 NUL 的奇偶分布。

    ASCII 为主的文本在 UTF-16 下每 2 字节就有 1 个 NUL，且固定落在同一侧。
    """
    sample = raw_bytes[:4096]
    if len(sample) < 4:
        return ''
    even_nul = sum(1 for i in range(0, len(sample) - 1, 2) if sample[i] == 0)
    odd_nul = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
    half = len(sample) // 2
    if half and odd_nul / half > 0.3 and even_nul / half < 0.05:
        return 'utf-16-le'
    if half and even_nul / half > 0.3 and odd_nul / half < 0.05:
        return 'utf-16-be'
    return ''


def _detect_encoding(raw_bytes: bytes) -> str:
    for bom, enc in _BOMS:
        if raw_bytes.startswith(bom):
            return enc

    guessed = _looks_like_utf16(raw_bytes)
    if guessed:
        return guessed

    for enc in _ENCODING_CHAIN:
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return 'utf-8'


def decode_subtitle_bytes(raw: bytes) -> str:
    """按探测出的编码解码，并剥掉行首 BOM。

    `utf-16-le` 解码后 BOM 会变成正文首字符（`\\ufeff[Script Info]`），
    让 `[...]` 段头判断失配 —— 必须剥掉。
    """
    return raw.decode(_detect_encoding(raw)).lstrip('﻿')


def _read_raw_lines(path: str) -> list[str]:
    with open(path, 'rb') as f:
        raw = f.read()
    return decode_subtitle_bytes(raw).splitlines(True)


def subtitle_write_encoding(path: str, default: str = 'utf-8-sig') -> str:
    """写出 `path` 时应使用的编码 —— 沿用其现有文件的编码。

    ASS 的行业惯例是 UTF-16LE + BOM，一律写 UTF-8 会把交付格式悄悄改掉。
    文件不存在时返回 `default`。
    """
    if not os.path.exists(path):
        return default
    with open(path, 'rb') as f:
        enc = _detect_encoding(f.read())
    # 'utf-16' 会写出 BOM 并采用本机字节序（LE）；_detect_encoding
    # 返回的 'utf-16-le' 单独用 open() 是不带 BOM 的，不能直接用。
    return 'utf-16' if enc.startswith('utf-16') else enc


def _write_raw_lines(path: str, lines: list[str], encoding: str = 'utf-8-sig'):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    # newline='' — lines already carry their terminators (splitlines(True)).
    # Text-mode translation would rewrite an existing '\r\n' as '\r\r\n'.
    with open(path, 'w', encoding=encoding, newline='') as f:
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


def write_subtitles(path: str, cues: list[dict], template_path: str = None,
                    style: str = 'Default'):
    """Write cue list back to subtitle file. Auto-detects SRT vs ASS.

    Cues are re-numbered sequentially (1, 2, 3...) for SRT.
    Start/end timecodes are normalized to SRT comma format.

    Args:
        path: Output file path.
        cues: Cue list to write.
        template_path: For ASS output, read header/styles from this file
                       if the output doesn't exist yet (new translation).
        style: For ASS output in new-file mode, the style name to use.
    """
    if path.lower().endswith('.ass'):
        return _write_ass_cues(path, cues, template_path=template_path,
                               style=style)
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

    # Fallback: match by line number (1-based).
    # A cue's block ends where the next cue starts — bounding by the next cue's
    # _start_line is exact for any text-line count. (A fixed `line_idx - sl <= 4`
    # window instead swallows the *next* cue's index line: with `_start_line` on
    # the index line, offset 4 already belongs to the following cue — that is how
    # a delete_line once removed the wrong subtitle.)
    line_num = fix.get('line', 0)
    if line_num:
        line_idx = line_num - 1  # 0-based
        for i, cue in enumerate(cues):
            sl = cue.get('_start_line', -1)
            if sl < 0 or sl > line_idx:
                continue
            nxt = cues[i + 1].get('_start_line', -1) if i + 1 < len(cues) else -1
            if nxt < 0:
                nxt = sl + 5  # last cue: no next block to bound by — cap the span
            if line_idx < nxt:
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
    _write_raw_lines(path, lines, encoding=subtitle_write_encoding(path))


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


def _ass_time(srt_time: str) -> str:
    """SRT 时间码 → ASS 时间码（`0:01:23.46`，百分秒，小时不补零）。"""
    h, m, rest = srt_time.split(':')
    s, ms = rest.replace(',', '.').split('.')
    return f'{int(h)}:{m}:{s}.{int(round(int(ms[:3]) / 10)):02d}'


def _write_ass_cues(path: str, cues: list[dict], template_path: str = None,
                    style: str = 'Default'):
    """Write cue list to ASS file.

    Two modes:

    * **New file** (``path`` doesn't exist) — the template supplies the
      header/styles only; the whole `[Events]` section is rebuilt from
      ``cues``.  Used for new translations.
    * **Existing file** — dialogue lines are edited in place, matched to the
      template by ``_start_line``.

    ⚠️ The mode matters.  In-place editing keys off ``_start_line``, i.e. the
    cue's position in *that same file*.  Feeding a template plus cues parsed
    from a *different* source (an SRT, another episode) matches no line at
    all and silently writes the template's own dialogue back out — output
    that looks like a success and is entirely the wrong subtitles.

    Args:
        path: Output ASS file path.
        cues: Cue list. Each needs start/end timecodes and text.
        template_path: Source of the header/styles (new-file mode) or the
            file being edited (existing-file mode).
        style: ASS style for new-file mode's dialogue lines.
    """
    from lib.ass_utils import read_ass_file, build_dialogue_line, write_ass_file

    is_new = not os.path.exists(path)
    # If output doesn't exist, use template for header/styles
    read_path = path if not is_new else (template_path or path)
    if not os.path.exists(read_path):
        raise FileNotFoundError(
            f'ASS output "{path}" does not exist and no template_path provided. '
            f'Pass template_path= to write_subtitles() for new translations.'
        )

    lines = read_ass_file(read_path)

    if is_new:
        ev = next((i for i, l in enumerate(lines)
                   if l.strip().lower() == '[events]'), None)
        if ev is None:
            raise ValueError(f'template ASS "{read_path}" has no [Events] section')
        fmt = next((i for i in range(ev, len(lines))
                    if lines[i].startswith('Format:')), None)
        if fmt is None:
            raise ValueError(f'template ASS "{read_path}" has no Events Format: line')

        out = list(lines[:fmt + 1])
        for c in cues:
            out.append(build_dialogue_line({
                'format': 'Dialogue: 0', 'layer': '0',
                'start': _ass_time(c['start']), 'end': _ass_time(c['end']),
                'style': style, 'name': '',
                'margin_l': '0', 'margin_r': '0', 'margin_v': '0',
                'effect': '', 'text': c['text'],
            }) + '\n')
        write_ass_file(path, out, template_path=read_path)
        return
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

    _write_raw_lines(path, lines, encoding=subtitle_write_encoding(path))


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
