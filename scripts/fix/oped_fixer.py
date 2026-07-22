#!/usr/bin/env python3
"""OP/ED fixer — detect, classify, and fix OP/ED regions across episodes.

Two modes of operation:

  Instrumental OP/ED (no vocals):
    Whisper hallucinates gibberish on pure music ("me", "ni", "car"...).
    → Cross-episode time clustering detects consistent hallucination patterns.
    → Auto-clean: replace hallucinations with [音楽], preserve real dialogue.

  Vocal OP/ED (with lyrics):
    Same song, same lyrics across episodes, but Whisper produces variants.
    → Cross-episode comparison finds variants for each lyric line.
    → AI review candidates generated → Claude fills canonical form.
    → Unified replacement across all episodes.

Key principle: cross-episode similarity is the signal.
  - Text SIMILAR across ≥3 episodes at same time → OP/ED content
  - Text UNIQUE per episode → real dialogue (don't touch)

Usage:
  # Generate AI review candidates (vocal OP/ED) or auto-clean (instrumental)
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json

  # Also generate AI review file for vocal OP/ED
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json \
      --ai-review temp/scans/oped_ai_review.json

  # Apply only auto-clean (skip AI review — for instrumental-only projects)
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json --auto-only
"""

import json
import os
import re
import sys
import argparse
from collections import defaultdict, Counter
from dataclasses import dataclass, field

import lib._path  # noqa: F401
from lib.whisper_utils import (
    OP_BOUNDARY_SEC, ED_BOUNDARY_SEC,
    parse_srt, write_srt, meaningful_jp_count,
)


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

# Minimum episodes needed at a time position to consider it OP/ED content
MIN_EPISODES_FOR_CLUSTER = 3

# Time tolerance for bucketing cues across episodes (± seconds)
TIME_TOLERANCE_SEC = 2.0

# Similarity threshold: if text similarity across episodes is below this,
# the content is considered unique (dialogue, not OP/ED)
SIMILARITY_THRESHOLD = 0.4

# Short text that's likely noise (characters or less)
NOISE_MAX_LEN = 3


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _text_similarity(a: str, b: str) -> float:
    """Simple character-level Jaccard similarity for Japanese text."""
    a = a.strip()
    b = b.strip()
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _is_noise(text: str) -> bool:
    """Check if text is Whisper hallucination noise, not real language.

    Noise patterns:
      - Very short (≤2 chars, except valid Japanese particles)
      - Pure Latin alphabet (no kana/kanji)
      - Repetitive single character ("ああああああ")
      - Latin ratio > 50% with no CJK
    """
    t = text.strip()
    if not t:
        return True
    if len(t) <= NOISE_MAX_LEN:
        # Short, but could be valid like "はい" or "うん"
        has_kana = bool(re.search(r'[ぁ-ヿ]', t))
        has_kanji = bool(re.search(r'[一-鿿]', t))
        has_latin = bool(re.search(r'[a-zA-Z]', t))
        if has_latin and not has_kana and not has_kanji:
            return True  # Pure Latin short text → noise
        if not has_kana and not has_kanji and not has_latin:
            return True  # Pure numbers/symbols → noise
        # Single kana could be noise or valid — check repetitiveness
        if len(t) <= 1 and has_kana:
            return True  # Single kana → likely noise
        return False  # 2-3 char kana/kanji → could be valid

    has_kana = bool(re.search(r'[ぁ-ヿ]', t))
    has_kanji = bool(re.search(r'[一-鿿]', t))
    has_latin = bool(re.search(r'[a-zA-Z]', t))

    # Pure Latin, no Japanese characters
    if has_latin and not has_kana and not has_kanji:
        latin_chars = sum(1 for c in t if c.isascii() and c.isalpha())
        if latin_chars / len(t) > 0.5:
            return True

    # Repetitive: same char > 70% of string
    if len(t) >= 4:
        char_counts = Counter(t)
        most_common_ratio = char_counts.most_common(1)[0][1] / len(t)
        if most_common_ratio > 0.7:
            return True

    return False


def _is_valid_japanese(text: str) -> bool:
    """Check if text looks like real Japanese content (not noise)."""
    t = text.strip()
    if len(t) < 2:
        return False
    has_kana = bool(re.search(r'[ぁ-ヿ]', t))
    has_kanji = bool(re.search(r'[一-鿿]', t))
    if not (has_kana or has_kanji):
        return False
    latin_ratio = sum(1 for c in t if c.isascii() and c.isalpha()) / max(len(t), 1)
    if latin_ratio > 0.5:
        return False
    return True


def _is_music_marker(text: str) -> bool:
    """Check if text is already a music/sound marker like [音楽] or [拍手]."""
    t = text.strip()
    return bool(re.match(r'^\[[^\]]+\]$', t))


# ═══════════════════════════════════════════════════════════════
# Language detection
# ═══════════════════════════════════════════════════════════════

def _detect_script(text: str) -> str:
    """Detect the dominant script of a text.

    Returns: 'cjk' (Chinese/Japanese kanji), 'kana' (hiragana/katakana),
             'cyrillic', 'latin', or 'unknown'.
    """
    t = text.strip()
    if not t:
        return 'unknown'
    cjk = len(re.findall(r'[一-鿿]', t))
    kana = len(re.findall(r'[ぁ-ヿ]', t))
    cyrillic = len(re.findall(r'[Ѐ-ӿ]', t))
    latin = len(re.findall(r'[a-zA-Z]', t))

    # Prioritize: CJK > Kana > Cyrillic > Latin
    if cjk > max(kana, cyrillic, latin):
        return 'cjk'
    if kana > max(cjk, cyrillic, latin):
        return 'kana'
    if cyrillic > max(cjk, kana, latin):
        return 'cyrillic'
    if latin > 0:
        return 'latin'
    return 'unknown'


def _script_to_lang(script: str) -> str:
    """Map script to language code for display purposes."""
    return {'cjk': 'zh/ja', 'kana': 'ja', 'cyrillic': 'ru',
            'latin': 'en/romaji', 'unknown': '??'}.get(script, script)


def _detect_corpus_lang(cues: list, sample_size: int = 50) -> str:
    """Detect the dominant language of a set of cues.

    Samples non-noise cues to determine the primary script.
    Returns language code: 'zh', 'ja', 'ru', 'en', or '??'.
    """
    scripts = Counter()
    for cue in cues[:sample_size * 2]:
        text = cue.get('text', '') if isinstance(cue, dict) else str(cue)
        if _is_noise(text) or _is_music_marker(text):
            continue
        script = _detect_script(text)
        scripts[script] += 1
        if sum(scripts.values()) >= sample_size:
            break

    if not scripts:
        return '??'
    dominant = scripts.most_common(1)[0][0]
    # cjk without kana → likely Chinese; cjk with kana → Japanese
    if dominant == 'cjk':
        return 'zh'
    if dominant == 'kana':
        return 'ja'
    return _script_to_lang(dominant).split('/')[0]


def _scripts_match(ref_script: str, target_lang: str) -> bool:
    """Check if reference script matches the target language."""
    if target_lang == 'zh':
        return ref_script == 'cjk'
    if target_lang == 'ja':
        return ref_script in ('cjk', 'kana')
    if target_lang == 'ru':
        return ref_script == 'cyrillic'
    return False


@dataclass
class TimeCluster:
    """A group of cues at the same time position across episodes."""
    bucket_start: float  # reference start time in seconds
    region: str  # 'OP' or 'ED'
    entries: list = field(default_factory=list)  # [{ep, fname, text, start, end, index}]
    is_instrumental: bool = False
    is_dialogue: bool = False  # unique text per episode → not OP/ED
    noise_count: int = 0
    valid_count: int = 0
    canonical_text: str = ''  # filled by auto or AI
    variants: dict = field(default_factory=dict)  # {text: count}


# ═══════════════════════════════════════════════════════════════
# OpedFixer
# ═══════════════════════════════════════════════════════════════

class OpedFixer:
    """Detect and fix OP/ED regions across episodes.

    Three-tier resource priority (matching overall subtitle logic):
      1. Reference subtitles (source language) → canonical, no AI needed
      2. Audio (Whisper) → cross-episode comparison + AI review
      3. Nothing → cross-episode comparison + AI review (current behavior)

    Modes:
      - auto: classify instrumental vs vocal, clean up instrumental noise
      - ai_review: generate candidates for vocal OP/ED → AI fills canonical form
      - reference: use reference subtitles as canonical (--reference <dir>)
    """

    def __init__(self, target_dir: str, *,
                 lang: str = 'ja',
                 op_boundary: float = OP_BOUNDARY_SEC,
                 ed_boundary: float = ED_BOUNDARY_SEC,
                 min_episodes: int = MIN_EPISODES_FOR_CLUSTER,
                 time_tolerance: float = TIME_TOLERANCE_SEC,
                 reference_dir: str = None):
        self.target_dir = target_dir
        self.lang = lang
        self.op_boundary = op_boundary
        self.ed_boundary = ed_boundary
        self.min_episodes = min_episodes
        self.time_tolerance = time_tolerance
        self.reference_dir = reference_dir

        # Collected data
        self.episodes: dict = {}  # {fname: {op_cues, ed_cues, max_end}}
        self.clusters: list[TimeCluster] = []
        self.instrumental_clusters: list[TimeCluster] = []
        self.vocal_clusters: list[TimeCluster] = []
        self.dialogue_clusters: list[TimeCluster] = []

        # Reference data: {(region, bucket_start_s): canonical_text}
        self.reference_texts: dict[tuple, str] = {}

    # ── Step 1: Collect ──────────────────────────────────────────

    def collect(self):
        """Scan all SRT files and collect OP/ED region cues."""
        srt_files = sorted([
            f for f in os.listdir(self.target_dir)
            if f.endswith('.srt')
        ])
        if not srt_files:
            print('[oped] No SRT files found.', file=sys.stderr)
            return

        for fname in srt_files:
            fpath = os.path.join(self.target_dir, fname)
            cues = list(parse_srt(fpath, mark_garbled=False))
            if not cues:
                continue

            max_end = max(c['end_s'] for c in cues)
            ed_start = max(0, max_end - self.ed_boundary)

            op_cues = [
                {'index': i, 'text': c['text'], 'start': c['start'],
                 'start_s': c['start_s'], 'end': c['end'], 'end_s': c['end_s']}
                for i, c in enumerate(cues)
                if c['start_s'] < self.op_boundary
            ]
            ed_cues = [
                {'index': i, 'text': c['text'], 'start': c['start'],
                 'start_s': c['start_s'], 'end': c['end'], 'end_s': c['end_s']}
                for i, c in enumerate(cues)
                if c['start_s'] > ed_start
            ]

            self.episodes[fname] = {
                'op_cues': op_cues,
                'ed_cues': ed_cues,
                'max_end': max_end,
            }

        print(f'[oped] Collected OP/ED cues from {len(self.episodes)} episodes',
              file=sys.stderr)

    def collect_reference(self):
        """Extract OP/ED cues from reference subtitle files (SRT or ASS).

        Reference files provide canonical text for OP/ED time positions.
        Supports both SRT (via parse_srt) and ASS (simple Dialogue parser).
        Cues are bucketed by (region, start_s) with ±time_tolerance matching.
        """
        if not self.reference_dir or not os.path.isdir(self.reference_dir):
            return

        ref_cues = []  # [(start_s, region, text), ...]

        for fname in sorted(os.listdir(self.reference_dir)):
            fpath = os.path.join(self.reference_dir, fname)
            lower = fname.lower()

            if lower.endswith('.srt'):
                cues = list(parse_srt(fpath, mark_garbled=False))
                if not cues:
                    continue
                max_end = max(c['end_s'] for c in cues)
                ed_start = max(0, max_end - self.ed_boundary)

                for c in cues:
                    region = None
                    if c['start_s'] < self.op_boundary:
                        region = 'OP'
                    elif c['start_s'] > ed_start:
                        region = 'ED'
                    if region:
                        ref_cues.append((c['start_s'], region, c['text'].strip()))

            elif lower.endswith('.ass'):
                # Simple ASS parser: extract Dialogue lines
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Get max end time from all Dialogue lines to compute ED boundary
                max_end = 0.0
                parsed = []
                for line in content.split('\n'):
                    if not line.startswith('Dialogue:'):
                        continue
                    parts = line.split(',', 9)
                    if len(parts) < 10:
                        continue
                    start_str, end_str = parts[1].strip(), parts[2].strip()
                    text = parts[9].strip()
                    # Strip {...} override tags
                    text = re.sub(r'\{[^}]*\}', '', text)
                    # ASS \N → space
                    text = text.replace('\\N', ' ').strip()
                    if not text:
                        continue

                    start_s = self._ass_time_to_sec(start_str)
                    end_s = self._ass_time_to_sec(end_str)
                    parsed.append((start_s, end_s, text))

                if not parsed:
                    continue
                max_end = max(p[1] for p in parsed)
                ed_start = max(0, max_end - self.ed_boundary)

                for start_s, end_s, text in parsed:
                    region = None
                    if start_s < self.op_boundary:
                        region = 'OP'
                    elif start_s > ed_start:
                        region = 'ED'
                    if region:
                        ref_cues.append((start_s, region, text))

        if not ref_cues:
            return

        # Bucket reference cues by time position (same logic as main clustering)
        for start_s, region, text in ref_cues:
            bucket_key = self._find_ref_bucket(start_s, region)
            if bucket_key is None:
                bucket_key = (region, start_s)
            # First occurrence wins (reference is authoritative)
            if bucket_key not in self.reference_texts:
                self.reference_texts[bucket_key] = text

        print(f'[oped] Reference: {len(ref_cues)} cues → '
              f'{len(self.reference_texts)} unique time positions '
              f'from {self.reference_dir}',
              file=sys.stderr)

    def _find_ref_bucket(self, start_s: float, region: str) -> tuple | None:
        """Find existing reference bucket within time tolerance."""
        for (r, bucket_start) in self.reference_texts:
            if r == region and abs(start_s - bucket_start) <= self.time_tolerance:
                return (r, bucket_start)
        return None

    @staticmethod
    def _ass_time_to_sec(tc: str) -> float:
        """Convert ASS timecode (H:MM:SS.cc) to seconds."""
        tc = tc.strip()
        parts = tc.split(':')
        h = int(parts[0])
        m = int(parts[1])
        s_parts = parts[2].split('.')
        sec = int(s_parts[0])
        cs = int(s_parts[1]) if len(s_parts) > 1 else 0  # centiseconds
        return h * 3600 + m * 60 + sec + cs / 100.0

    # ── Step 2: Cluster ──────────────────────────────────────────

    def cluster(self):
        """Group cues by time position across episodes using bucketing."""
        all_buckets: dict[tuple, dict] = {}  # {(region, bucket_start): {...}}

        for fname, ep_data in self.episodes.items():
            for region, key in [('OP', 'op_cues'), ('ED', 'ed_cues')]:
                for cue in ep_data[key]:
                    # Find or create bucket
                    bucket_start = self._find_bucket(cue['start_s'], region, all_buckets)
                    if bucket_start is None:
                        bucket_start = cue['start_s']
                        all_buckets[(region, bucket_start)] = {
                            'region': region,
                            'bucket_start': bucket_start,
                            'entries': [],
                        }
                    all_buckets[(region, bucket_start)]['entries'].append({
                        'ep': fname,
                        'text': cue['text'],
                        'start': cue['start'],
                        'start_s': cue['start_s'],
                        'end': cue['end'],
                        'end_s': cue['end_s'],
                        'index': cue['index'],
                    })

        # Build TimeCluster objects
        for (region, bucket_start), bucket_data in all_buckets.items():
            entries = bucket_data['entries']
            ep_count = len(set(e['ep'] for e in entries))

            if ep_count < self.min_episodes:
                continue  # Not enough data for cross-episode comparison

            cluster = TimeCluster(
                bucket_start=bucket_start,
                region=region,
                entries=entries,
            )

            # Classify each entry's text
            noise_count = 0
            valid_count = 0
            text_counts = Counter(e['text'] for e in entries)
            cluster.variants = dict(text_counts)

            for text, count in text_counts.items():
                if _is_music_marker(text):
                    continue  # Already a marker, don't count
                if _is_noise(text):
                    noise_count += count
                elif _is_valid_japanese(text):
                    valid_count += count

            cluster.noise_count = noise_count
            cluster.valid_count = valid_count

            # Classify cluster type
            self._classify_cluster(cluster)

        print(f'[oped] {len(self.clusters)} time clusters formed '
              f'(instrumental={len(self.instrumental_clusters)}, '
              f'vocal={len(self.vocal_clusters)}, '
              f'dialogue={len(self.dialogue_clusters)})',
              file=sys.stderr)

    def _find_bucket(self, start_s: float, region: str,
                     buckets: dict) -> float | None:
        """Find existing bucket within time tolerance."""
        for (r, bucket_start) in buckets:
            if r == region and abs(start_s - bucket_start) <= self.time_tolerance:
                return bucket_start
        return None

    def _classify_cluster(self, cluster: TimeCluster):
        """Classify a time cluster as instrumental, vocal, or dialogue.

        Decision logic:
          1. If all text is noise → instrumental (hallucination on music)
          2. If all text is unique (low cross-ep similarity) → dialogue
          3. If mixed (valid JP with some variants) → vocal OP/ED
        """
        self.clusters.append(cluster)

        # Check cross-episode text similarity
        texts_by_ep = defaultdict(list)
        for entry in cluster.entries:
            texts_by_ep[entry['ep']].append(entry['text'])

        # If all entries are noise → instrumental
        total = cluster.noise_count + cluster.valid_count
        if total > 0 and cluster.noise_count / total >= 0.8:
            cluster.is_instrumental = True
            self.instrumental_clusters.append(cluster)
            return

        # Check if texts are similar across episodes
        all_texts = list(set(e['text'] for e in cluster.entries))
        if len(all_texts) <= 1:
            # All identical — could be OP/ED lyrics already correct, or noise
            if _is_noise(all_texts[0]) if all_texts else True:
                cluster.is_instrumental = True
                self.instrumental_clusters.append(cluster)
            else:
                # All same valid JP → already unified, no fix needed
                pass
            return

        # Compute average pairwise similarity
        similarities = []
        for i in range(len(all_texts)):
            for j in range(i + 1, len(all_texts)):
                sim = _text_similarity(all_texts[i], all_texts[j])
                similarities.append(sim)

        avg_sim = sum(similarities) / len(similarities) if similarities else 0

        if avg_sim < SIMILARITY_THRESHOLD:
            # Low similarity → each episode has unique text → dialogue
            cluster.is_dialogue = True
            self.dialogue_clusters.append(cluster)
        elif cluster.valid_count > 0:
            # Some meaningful JP with variants → vocal OP/ED
            self.vocal_clusters.append(cluster)
        else:
            # Noise with some similarity (repeated hallucination pattern)
            cluster.is_instrumental = True
            self.instrumental_clusters.append(cluster)

    # ── Step 3: Generate fixes ───────────────────────────────────

    def generate_auto_fixes(self) -> list[dict]:
        """Generate fixes for instrumental OP/ED (auto-clean hallucinations).

        For each instrumental cluster:
          - Replace noise text with [音楽]
          - Skip cues that are already [音楽] or [拍手]
        """
        fixes = []
        for cluster in self.instrumental_clusters:
            for entry in cluster.entries:
                text = entry['text'].strip()
                if _is_music_marker(text):
                    continue  # Already a marker
                if not _is_noise(text) and _is_valid_japanese(text):
                    continue  # Real Japanese in an instrumental cluster → keep

                fixes.append({
                    'action': 'replace_text',
                    'file': entry['ep'],
                    'start': entry['start'],
                    'original': text,
                    'replacement': '[音楽]',
                    'note': (f'{cluster.region} 器楽区間 Whisper幻覚 → [音楽] '
                             f'(クラスタ {cluster.bucket_start:.1f}s, '
                             f'{len(set(e["ep"] for e in cluster.entries))}話中)'),
                })

        # Sort: by file, then start time
        fixes.sort(key=lambda f: (f['file'], f['start']))
        return fixes

    def generate_ai_review(self) -> dict:
        """Generate AI review candidates for vocal OP/ED.

        Returns a dict suitable for writing to oped_ai_review.json.
        AI fills 'canonical' for each group; leave empty to skip.
        Set 'canonical': '__INSTRUMENTAL__' to treat as instrumental.

        When reference subtitles are available, 'reference_text' is populated
        and 'canonical' is auto-filled from reference (AI can override).
        """
        has_reference = len(self.reference_texts) > 0
        auto_canonical_count = 0

        candidates = []
        for cluster in self.vocal_clusters:
            # Filter to meaningful variants only
            variants = {}
            noise_variants = {}
            for text, count in cluster.variants.items():
                if _is_music_marker(text):
                    continue
                if _is_noise(text):
                    noise_variants[text] = count
                else:
                    variants[text] = count

            if not variants:
                continue  # All noise, should have been classified instrumental

            # Find most common variant as suggestion
            all_var = {**variants, **noise_variants}
            suggested = max(variants.items(), key=lambda x: x[1]) if variants else ('', 0)

            # Look up reference text by time position
            ref_text = ''
            for (region, bucket_start), text in self.reference_texts.items():
                if (region == cluster.region and
                        abs(bucket_start - cluster.bucket_start) <= self.time_tolerance):
                    ref_text = text
                    break

            # Detect reference language and check if it matches target
            ref_script = _detect_script(ref_text) if ref_text else ''
            ref_lang = _script_to_lang(ref_script) if ref_script else ''
            needs_translation = bool(ref_text) and not _scripts_match(ref_script, self.lang)

            # Auto-fill canonical from reference ONLY if same language
            canonical = ''
            if ref_text and not needs_translation:
                canonical = ref_text
                auto_canonical_count += 1

            candidates.append({
                'region': cluster.region,
                'time_position_s': cluster.bucket_start,
                'episode_count': len(set(e['ep'] for e in cluster.entries)),
                'variants': variants,
                'noise_variants': noise_variants,
                'suggested_canonical': suggested[0],
                'suggested_confidence': (
                    suggested[1] / sum(all_var.values())
                    if sum(all_var.values()) > 0 else 0
                ),
                'reference_text': ref_text,
                'reference_lang': ref_lang,
                'needs_translation': needs_translation,
                'canonical': canonical,
                'sample_times': [
                    {'ep': e['ep'], 'start': e['start'], 'text': e['text']}
                    for e in cluster.entries[:5]
                ],
            })

        # Detect actual corpus language from variants
        all_cues_for_lang = []
        for cluster in self.vocal_clusters:
            for entry in cluster.entries:
                all_cues_for_lang.append(entry)
        detected_lang = _detect_corpus_lang(all_cues_for_lang) if all_cues_for_lang else self.lang

        translation_needed_count = sum(1 for c in candidates if c.get('needs_translation'))

        desc = (
            'OP/ED AI Review Candidates — vocal OP/ED lyric variants across episodes.\n'
            'For each candidate group:\n'
            '  - Fill "canonical" with the correct lyrics IN THE TARGET LANGUAGE.\n'
            '  - Set "canonical": "__INSTRUMENTAL__" if this is actually instrumental.\n'
            '  - Leave "canonical": "" to skip (no fix applied).\n'
            '  - "suggested_canonical" is the majority-vote text; override if wrong.'
        )
        if has_reference:
            if translation_needed_count > 0:
                desc += (
                    f'\n  - ⚠️ {translation_needed_count} candidates need TRANSLATION:\n'
                    f'    reference_lang ({ref_lang}) ≠ target_lang ({self.lang}/{detected_lang}).\n'
                    '  - AI must TRANSLATE reference_text to the target language,\n'
                    '    then fill canonical with the translation.\n'
                    '  - Use Baidu Translate or AI translation for reference_text.'
                )
            else:
                desc += (
                    '\n  - "reference_text" is from --reference subtitles '
                    '(same language, auto-filled as canonical).\n'
                    '  - AI should validate: correct → leave as-is; wrong → override.'
                )

        return {
            'description': desc,
            'has_reference': has_reference,
            'target_lang': self.lang,
            'detected_lang': detected_lang,
            'auto_canonical_from_reference': auto_canonical_count,
            'needs_translation': translation_needed_count,
            'total_groups': len(candidates),
            'op_groups': sum(1 for c in candidates if c['region'] == 'OP'),
            'ed_groups': sum(1 for c in candidates if c['region'] == 'ED'),
            'candidates': candidates,
        }

    def apply_ai_fixes(self, ai_review_path: str) -> list[dict]:
        """Read AI-reviewed file and generate fixes for all cluster entries.

        Uses in-memory cluster data (self.vocal_clusters) to generate fixes for
        ALL entries, not just the sample_times preview in the JSON.
        """
        with open(ai_review_path, 'r', encoding='utf-8') as f:
            review = json.load(f)

        # Build lookup: (region, time_position_s) → canonical
        decisions = {}
        for candidate in review.get('candidates', []):
            canonical = candidate.get('canonical', '').strip()
            if not canonical:
                continue
            key = (candidate['region'], candidate['time_position_s'])
            decisions[key] = canonical

        if not decisions:
            return []

        # Match decisions to in-memory clusters and generate fixes
        fixes = []
        matched_clusters = 0
        for cluster in self.vocal_clusters:
            key = (cluster.region, cluster.bucket_start)
            canonical = decisions.get(key)
            if not canonical:
                continue
            matched_clusters += 1

            if canonical == '__INSTRUMENTAL__':
                # AI determined this is instrumental → clean up all entries
                for entry in cluster.entries:
                    text = entry['text'].strip()
                    if _is_music_marker(text):
                        continue
                    fixes.append({
                        'action': 'replace_text',
                        'file': entry['ep'],
                        'start': entry['start'],
                        'original': text,
                        'replacement': '[音楽]',
                        'note': (f'{cluster.region} AI判定:器楽 → [音楽] '
                                 f'(クラスタ {cluster.bucket_start:.1f}s)'),
                    })
            else:
                # Apply canonical text to all non-matching entries
                for entry in cluster.entries:
                    text = entry['text'].strip()
                    if text == canonical:
                        continue  # Already correct
                    fixes.append({
                        'action': 'replace_text',
                        'file': entry['ep'],
                        'start': entry['start'],
                        'original': text,
                        'replacement': canonical,
                        'note': (f'{cluster.region} 歌詞統一: {canonical}'),
                    })

        if matched_clusters > 0:
            print(f'[oped] AI review: {matched_clusters}/{len(decisions)} '
                  f'decisions matched to clusters → {len(fixes)} raw fixes',
                  file=sys.stderr)

        return fixes

    # ── Step 4: Run ──────────────────────────────────────────────

    def run(self, *, ai_review_output: str = None,
            ai_review_input: str = None,
            auto_only: bool = False) -> dict:
        """Run the full OP/ED fix pipeline.

        Args:
            ai_review_output: path to write AI review candidates (vocal OP/ED)
            ai_review_input: path to read AI-reviewed fixes
            auto_only: skip AI review, only auto-clean instrumental

        Returns:
            {fixes: [...], summary: {...}}
        """
        # Collect reference first (if available) — tier 1: source language subtitles
        self.collect_reference()

        self.collect()
        if len(self.episodes) < 2:
            return {'fixes': [], 'summary': {'error': 'Need ≥2 episodes'}}

        self.cluster()

        fixes = []

        # Always generate auto-fixes for instrumental clusters
        auto_fixes = self.generate_auto_fixes()
        fixes.extend(auto_fixes)

        # Handle vocal OP/ED
        if ai_review_input:
            # Apply AI-reviewed fixes
            ai_fixes = self.apply_ai_fixes(ai_review_input)
            fixes.extend(ai_fixes)
        elif ai_review_output and not auto_only:
            # Generate AI review candidates
            review = self.generate_ai_review()
            with open(ai_review_output, 'w', encoding='utf-8') as f:
                json.dump(review, f, ensure_ascii=False, indent=2)
            print(f'[oped] AI review candidates → {ai_review_output}', file=sys.stderr)
            if review['total_groups'] == 0:
                print('[oped] No vocal OP/ED detected — all instrumental or already unified.',
                      file=sys.stderr)

        # Deduplicate fixes
        seen = set()
        deduped = []
        for fix in fixes:
            key = (fix['action'], fix['file'], fix['start'])
            if key not in seen:
                seen.add(key)
                deduped.append(fix)

        summary = {
            'episodes': len(self.episodes),
            'total_clusters': len(self.clusters),
            'instrumental_clusters': len(self.instrumental_clusters),
            'vocal_clusters': len(self.vocal_clusters),
            'dialogue_clusters': len(self.dialogue_clusters),
            'auto_fixes': len(auto_fixes),
            'ai_fixes': len(fixes) - len(auto_fixes),
            'total_fixes': len(deduped),
        }

        return {'fixes': deduped, 'summary': summary}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description='OP/ED fixer — detect & fix OP/ED regions across episodes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-clean instrumental only (for projects without OP/ED vocals)
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json --auto-only

  # Full: auto-clean + generate AI review for vocal OP/ED
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json \\
      --ai-review temp/scans/oped_ai_review.json

  # Apply AI-reviewed fixes
  python oped_fixer.py AI审查后/ --lang ja -o temp/scans/oped_fixes.json \\
      --apply-ai-review temp/scans/oped_ai_review.json
        """
    )
    parser.add_argument('target_dir', help='Directory containing SRT files')
    parser.add_argument('--lang', default='auto',
                        help='Target language: auto (detect), ja, zh. Default: auto-detect.')
    parser.add_argument('--output', '-o', help='Output JSON path for fixes')
    parser.add_argument('--ai-review',
                        help='Path to write AI review candidates (vocal OP/ED)')
    parser.add_argument('--apply-ai-review',
                        help='Path to read AI-reviewed file and generate fixes')
    parser.add_argument('--auto-only', action='store_true',
                        help='Only auto-clean instrumental OP/ED, skip AI review')
    parser.add_argument('--op-boundary', type=float, default=OP_BOUNDARY_SEC,
                        help=f'OP boundary in seconds (default: {OP_BOUNDARY_SEC})')
    parser.add_argument('--ed-boundary', type=float, default=ED_BOUNDARY_SEC,
                        help=f'ED boundary in seconds (default: {ED_BOUNDARY_SEC})')
    parser.add_argument('--min-episodes', type=int, default=MIN_EPISODES_FOR_CLUSTER,
                        help=f'Min episodes for cluster (default: {MIN_EPISODES_FOR_CLUSTER})')
    parser.add_argument('--reference',
                        help='Directory of reference subtitles (SRT/ASS) with correct OP/ED '
                             'lyrics. Source language subtitles → canonical text. '
                             'When provided, reference_text is auto-filled as canonical '
                             'in AI review (AI validates, not translates).')
    args = parser.parse_args()

    if not os.path.isdir(args.target_dir):
        print(f'ERROR: {args.target_dir} not found or not a directory.', file=sys.stderr)
        sys.exit(1)

    # ── Resolve language: auto-detect or use explicit --lang ──
    if args.lang == 'auto':
        from lib.project_utils import detect_project_lang
        # Detect from the target_dir's parent (project root)
        project_dir = os.path.dirname(os.path.abspath(args.target_dir))
        lang = detect_project_lang(project_dir)
        print(f'[oped] Auto-detected language: {lang}', file=sys.stderr)
    else:
        lang = args.lang

    fixer = OpedFixer(
        args.target_dir,
        lang=lang,
        op_boundary=args.op_boundary,
        ed_boundary=args.ed_boundary,
        min_episodes=args.min_episodes,
        reference_dir=args.reference,
    )

    result = fixer.run(
        ai_review_output=args.ai_review,
        ai_review_input=args.apply_ai_review,
        auto_only=args.auto_only,
    )

    s = result['summary']
    print(f'\n=== OP/ED Fix Report ===')
    print(f'  Episodes:            {s.get("episodes", 0)}')
    print(f'  Time clusters:       {s.get("total_clusters", 0)}')
    print(f'  Instrumental:        {s.get("instrumental_clusters", 0)}')
    print(f'  Vocal (lyrics):      {s.get("vocal_clusters", 0)}')
    print(f'  Dialogue (skipped):  {s.get("dialogue_clusters", 0)}')
    print(f'  Auto-fixes:          {s.get("auto_fixes", 0)}')
    print(f'  AI-review fixes:     {s.get("ai_fixes", 0)}')
    print(f'  Total fixes:         {s.get("total_fixes", 0)}')

    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'\n[oped] → {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
