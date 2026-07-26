#!/usr/bin/env python3
"""Whisper pipeline — VAD clean → segment-first with auto-upgrade to full-episode.

v3.0: VAD pre-scan deletes non-speech cues before garbled detection.
      --separate-vocals now actually calls demucs.
      WebRTC VAD distinguishes speech from music/sfx (silencedetect can't).

Flow:
  1. Extract full audio → WebRTC VAD → speech timeline
  2. Delete ALL cues with no speech overlap ([音楽][拍手][笑い] + hallucinations)
  3. On remaining cues: garbled → Tier 1/2 Whisper fix

Tier 1 (segment): cluster garbled cues → extract segments → whisper → match
Tier 2 (full-episode): auto-upgrade when fragments > 15 → transcribe all → align → fix

Usage:
  python whisper_pipeline.py video.mkv sub.srt \
    --whisper-cli D:/.../whisper-cli.exe \
    --model D:/.../kotoba-q5_0.bin \
    --output fixes.json
"""

import sys, os, re, subprocess, json, argparse, tempfile, time, wave

import lib._path  # noqa: F401

from lib.whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds, format_tc,
    parse_srt, write_srt, apply_fixes_to_srt, run_whisper,
    extract_audio_wav, is_valid_subtitle_text,
    separate_vocals, get_audio_duration,
)
setup_windows_utf8()

# ── Tier 1 constants ──
MAX_CLUSTER_GAP = 60.0   # seconds — max gap between garbled cues in a cluster
UPGRADE_THRESHOLD = 15   # fragments > this → auto-upgrade to Tier 2
GAP_SEC = 5.0            # padding around clusters

_CPU_COUNT = os.cpu_count() or 4
_TARGET = max(1, int(_CPU_COUNT * 0.8))
DEFAULT_THREADS = max(1, _TARGET // 2)


# ═══════════════════════════════════════════════════════════════
# VAD pre-scan — delete non-speech cues before garbled detection
# ═══════════════════════════════════════════════════════════════

def get_speech_timeline(audio_path, aggressiveness=2):
    """Use WebRTC VAD to detect speech segments in 16kHz mono WAV.

    Unlike ffmpeg silencedetect (which only detects audio energy and can't
    distinguish speech from music), WebRTC VAD uses a Gaussian mixture model
    trained on speech — it correctly classifies background music as non-speech.

    Args:
        audio_path: 16kHz mono WAV
        aggressiveness: 0=least, 3=most aggressive filtering (default 2)

    Returns:
        [(start_s, end_s), ...] speech intervals
    """
    try:
        import webrtcvad
    except ImportError:
        print('⚠ webrtcvad 不可用，回退到 silencedetect (无法区分语音/音乐)',
              file=sys.stderr)
        return _get_speech_timeline_silencedetect(audio_path)

    with wave.open(audio_path, 'rb') as wf:
        rate = wf.getframerate()
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()

        if rate != 16000 or nchannels != 1 or sampwidth != 2:
            print(f'⚠ VAD requires 16kHz mono 16-bit, got {rate}Hz/{nchannels}ch/{sampwidth*8}bit',
                  file=sys.stderr)
            return _get_speech_timeline_silencedetect(audio_path)

        vad = webrtcvad.Vad(aggressiveness)
        frame_ms = 30
        frame_samples = int(rate * frame_ms / 1000)

        speech_segs = []
        in_speech = False
        speech_start = 0.0
        pos = 0
        total_frames = 0

        while True:
            frame = wf.readframes(frame_samples)
            frame_bytes = len(frame)
            if frame_bytes < frame_samples * 2:
                break
            try:
                is_speech = vad.is_speech(frame, rate)
            except Exception:
                is_speech = False

            t = pos * frame_ms / 1000.0
            total_frames += 1

            if is_speech and not in_speech:
                speech_start = t
                in_speech = True
            elif not is_speech and in_speech:
                if t - speech_start >= 0.3:  # min 300ms speech
                    speech_segs.append((speech_start, t))
                in_speech = False
            pos += 1

        if in_speech:
            final_t = total_frames * frame_ms / 1000.0
            if final_t - speech_start >= 0.3:
                speech_segs.append((speech_start, final_t))
    # Merge nearby segments (<0.5s gap)
    speech_segs = _merge_nearby_segments(speech_segs, gap=0.5)
    return speech_segs


def _get_speech_timeline_silencedetect(audio_path):
    """Fallback: ffmpeg silencedetect (can't distinguish speech from music)."""
    from lib.whisper_utils import vad_filter_audio
    segs, dur, _ = vad_filter_audio(audio_path, audio_path + '.vad_fallback.wav')
    # Clean up temp file
    try:
        os.remove(audio_path + '.vad_fallback.wav')
    except Exception:
        pass
    return segs


def _merge_nearby_segments(segs, gap=0.5):
    """Merge speech segments separated by short gaps (e.g. breath pauses)."""
    if not segs:
        return segs
    merged = [list(segs[0])]
    for ss, es in segs[1:]:
        if ss - merged[-1][1] <= gap:
            merged[-1][1] = es
        else:
            merged.append([ss, es])
    return [(s, e) for s, e in merged]


def cue_overlaps_speech(cue, speech_segs, min_overlap_s=0.0):
    """Check if cue overlaps with any speech segment by at least min_overlap_s."""
    cs, ce = cue['start_s'], cue['end_s']
    for ss, es in speech_segs:
        overlap = min(ce, es) - max(cs, ss)
        if overlap >= min_overlap_s:
            return True
    return False


# Non-dialogue markers — compiled per language on first use
_NON_DIALOGUE_CACHE = {}


def _get_non_dialogue_re(target_lang='ja'):
    """Get compiled regex for non-dialogue markers in the target language."""
    if target_lang not in _NON_DIALOGUE_CACHE:
        from lib.language_utils import get_lang_utils
        lu = get_lang_utils(target_lang)
        patterns = getattr(lu, 'NON_DIALOGUE_PATTERNS', [])
        _NON_DIALOGUE_CACHE[target_lang] = re.compile('|'.join(patterns)) if patterns else re.compile(r'$^')
    return _NON_DIALOGUE_CACHE[target_lang]


def is_non_dialogue_marker(text, target_lang='ja'):
    """Check if text is a known non-dialogue editorial marker (language-aware)."""
    return bool(_get_non_dialogue_re(target_lang).match(text.strip()))


def vad_delete_nonspeech(audio_path, cues, srt_path, target_lang='ja'):
    """Delete non-dialogue cues. Two-tier strategy:

    Tier A — Content-based: Always delete known editorial markers
              ([音楽], [拍手], [笑い], etc.), regardless of VAD.
              These are production notes, not dialogue.

    Tier B — Audio-based: For text-only cues, use WebRTC VAD to check
              if ANY speech exists in the cue's time range.
              Only delete if the cue has ZERO speech overlap AND is short (<3s).
              This catches Whisper hallucinations in silence without
              risking false deletion of valid short dialogue.

    Modifies SRT in-place.

    Returns:
        (kept_cues, deleted_cues): both lists of cue dicts
    """
    print('[VAD] Detecting speech segments ...', file=sys.stderr)
    speech_segs = get_speech_timeline(audio_path)
    speech_dur = sum(es - ss for ss, es in speech_segs)
    total_dur = get_audio_duration(audio_path) or 1
    print(f'[VAD] Speech: {speech_dur:.0f}s / {total_dur:.0f}s '
          f'({100*speech_dur/total_dur:.0f}%), {len(speech_segs)} segments',
          file=sys.stderr)

    kept, deleted = [], []
    for c in cues:
        text = c['text'].strip()

        # Tier A: known non-dialogue markers — always delete
        if is_non_dialogue_marker(text, target_lang):
            deleted.append(c)
            continue

        # Tier B: text cues — delete only if NO speech at all AND short
        # Safety filter: if text looks like readable dialogue, keep it even
        # if VAD says no speech (VAD can miss short/spoken lines).
        has_speech = cue_overlaps_speech(c, speech_segs, min_overlap_s=0.0)
        if not has_speech:
            from lib.whisper_utils import looks_like_plausible_text
            if looks_like_plausible_text(text, target_lang):
                kept.append(c)
                continue
            cue_dur = c['end_s'] - c['start_s']
            if cue_dur < 3.0:
                deleted.append(c)
                continue

        kept.append(c)

    if deleted:
        print(f'[VAD] Deleting {len(deleted)} cues:', file=sys.stderr)
        for c in deleted:
            reason = 'marker' if is_non_dialogue_marker(c['text'].strip()) else 'no speech'
            print(f'  [{c["start"]}] [{reason}] {c["text"][:60]}', file=sys.stderr)
        write_srt(srt_path, kept)
        print(f'[VAD] {len(kept)} cues remain in {os.path.basename(srt_path)}',
              file=sys.stderr)
    else:
        print('[VAD] All cues kept — nothing to delete.', file=sys.stderr)

    return kept, deleted, speech_segs


# ═══════════════════════════════════════════════════════════════
# VAD → missing subtitle detection
# ═══════════════════════════════════════════════════════════════

# Placeholder text that triggers garbled classification (contains Latin chars)
# ═══════════════════════════════════════════════════════════════
# Unified fix region builder (v5.3)
# ═══════════════════════════════════════════════════════════════

def build_fix_regions(speech_segs, cues, target_lang='ja',
                       min_overlap_s=0.3, min_gap_s=3.0, max_gap_s=45.0):
    """Build unified fix regions from VAD speech segments.

    Replaces BOTH build_clusters() AND find_missing_subtitle_gaps()
    with a single speech-driven mapping. The VAD speech segment is the
    atomic unit of dialogue — every fix region derives from it.

    For each VAD speech segment:
      1. Find all cues that overlap with it
      2. Classify:
         - Speech fully covered by clean cues → skip (no issue)
         - Speech overlaps ANY garbled cue → type 'garbled'
         - Speech partially overlaps clean cues AND extends beyond
           (≥ min_gap_s uncovered) → type 'partial_overlap'
         - Speech has no cue coverage → type 'missing'
      3. Record cues_to_clear for garbled + partial_overlap types

    Adjacent/overlapping regions are merged to avoid redundant Whisper
    calls on the same dialogue.

    Args:
        speech_segs: [(start_s, end_s), ...] from WebRTC VAD
        cues: list of cue dicts (must have is_garbled, start_s, end_s, text)
        target_lang: language code for is_non_dialogue_marker check
        min_overlap_s: minimum cue-speech overlap to be considered "overlapping"
        min_gap_s: minimum uncovered speech duration to trigger partial_overlap
        max_gap_s: skip speech segments longer than this (OP/ED songs)

    Returns:
        list of fix region dicts:
        [{
            'start_s': float, 'end_s': float,
            'type': 'garbled' | 'partial_overlap' | 'missing',
            'cues_to_clear': [{start, end, start_s, end_s, text, line}, ...],
            'garbled_cues': [...],     # subset with is_garbled=True
            'clean_cues': [...],       # subset with is_garbled=False
            'context_left': cue|None,  # nearest clean cue before region
            'context_right': cue|None, # nearest clean cue after region
        }]
    """
    if not cues or not speech_segs:
        return []

    # ── Pre-processing ──
    # Sort cues by start time and build index for fast lookup
    sorted_cues = sorted(cues, key=lambda c: c['start_s'])

    # Build covered intervals from all cues (for missing detection)
    covered = [(c['start_s'], c['end_s']) for c in sorted_cues]
    merged = []
    for ss, es in covered:
        if merged and ss <= merged[-1][1] + 2.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], es))
        else:
            merged.append((ss, es))

    first_cue_s = merged[0][0] if merged else 0
    last_cue_e = merged[-1][1] if merged else 0

    # ── Build regions per speech segment ──
    raw_regions = []

    for ss, es in speech_segs:
        # Skip speech entirely outside subtitle range (OP/ED music)
        if es <= first_cue_s or ss >= last_cue_e:
            continue
        # Clamp to inter-cue range
        ss = max(ss, first_cue_s)
        es = min(es, last_cue_e)

        dur = es - ss
        # Skip very long segments (OP/ED songs)
        if dur > max_gap_s:
            continue

        # Find overlapping cues
        overlaps = []
        for cue in sorted_cues:
            cue_ss, cue_es = cue['start_s'], cue['end_s']
            overlap = min(cue_es, es) - max(cue_ss, ss)
            if overlap >= min_overlap_s:
                overlaps.append(cue)

        # Classify
        garbled_in_overlaps = [c for c in overlaps if c.get('is_garbled')]
        clean_in_overlaps = [c for c in overlaps if not c.get('is_garbled')]

        if not overlaps:
            # No cue at all → missing subtitle
            raw_regions.append({
                'start_s': ss, 'end_s': es,
                'type': 'missing',
                'cues_to_clear': [],
                'garbled_cues': [],
                'clean_cues': [],
            })
            continue

        if garbled_in_overlaps:
            # Has garbled cues → fix them (and any clean cues in same utterance)
            cues_to_clear = overlaps  # Clear all: garbled + clean partials
            raw_regions.append({
                'start_s': ss, 'end_s': es,
                'type': 'garbled',
                'cues_to_clear': cues_to_clear,
                'garbled_cues': garbled_in_overlaps,
                'clean_cues': clean_in_overlaps,
            })
            continue

        # No garbled cues. Check for partial overlap (speech extends beyond cues)
        covered_start = min(c['start_s'] for c in overlaps)
        covered_end = max(c['end_s'] for c in overlaps)
        uncovered_before = covered_start - ss
        uncovered_after = es - covered_end
        total_uncovered = max(0, uncovered_before) + max(0, uncovered_after)

        if total_uncovered >= min_gap_s:
            # Speech meaningfully extends beyond existing cues → partial overlap
            raw_regions.append({
                'start_s': ss, 'end_s': es,
                'type': 'partial_overlap',
                'cues_to_clear': overlaps,
                'garbled_cues': [],
                'clean_cues': clean_in_overlaps,
            })
        # else: speech is well-covered by clean cues → skip

    if not raw_regions:
        return []

    # ── Merge adjacent/overlapping regions ──
    raw_regions.sort(key=lambda r: r['start_s'])
    merged_regions = [dict(raw_regions[0])]

    for r in raw_regions[1:]:
        prev = merged_regions[-1]
        # Merge if overlapping or within 2s
        if r['start_s'] <= prev['end_s'] + 2.0:
            # Expand time range
            prev['end_s'] = max(prev['end_s'], r['end_s'])
            # Merge type: garbled > partial_overlap > missing
            type_rank = {'garbled': 3, 'partial_overlap': 2, 'missing': 1}
            if type_rank.get(r['type'], 0) > type_rank.get(prev['type'], 0):
                prev['type'] = r['type']
            # Union cues_to_clear
            seen_starts = {c['start'] for c in prev['cues_to_clear']}
            for c in r['cues_to_clear']:
                if c['start'] not in seen_starts:
                    seen_starts.add(c['start'])
                    prev['cues_to_clear'].append(c)
            # Union garbled_cues
            seen_garbled = {c['start'] for c in prev['garbled_cues']}
            for c in r['garbled_cues']:
                if c['start'] not in seen_garbled:
                    seen_garbled.add(c['start'])
                    prev['garbled_cues'].append(c)
            # Union clean_cues
            seen_clean = {c['start'] for c in prev['clean_cues']}
            for c in r['clean_cues']:
                if c['start'] not in seen_clean:
                    seen_clean.add(c['start'])
                    prev['clean_cues'].append(c)
        else:
            merged_regions.append(dict(r))

    # ── Attach context cues (nearest clean neighbor before/after) ──
    for region in merged_regions:
        region['context_left'] = None
        region['context_right'] = None

        # Find all cues that are NOT in cues_to_clear
        clear_starts = {c['start'] for c in region['cues_to_clear']}
        clean_context_cues = [c for c in sorted_cues
                             if c['start'] not in clear_starts
                             and not c.get('is_garbled')
                             and c.get('text', '').strip()
                             and not is_non_dialogue_marker(c['text'], target_lang)]

        # Left context: last clean cue ending before this region
        for c in reversed(clean_context_cues):
            if c['end_s'] <= region['start_s'] + 2.0:
                region['context_left'] = c
                break

        # Right context: first clean cue starting after this region
        for c in clean_context_cues:
            if c['start_s'] >= region['end_s'] - 2.0:
                region['context_right'] = c
                break

    return merged_regions


# ═══════════════════════════════════════════════════════════════
# Tier 1: segment-based fix
# ═══════════════════════════════════════════════════════════════

def _is_context_text(whisper_text, left_text, right_text, threshold=0.5):
    """Check if whisper output is likely a transcription of a known context cue.

    When cluster audio is expanded to include adjacent clean cues (left/right),
    Whisper will transcribe them. This function detects those context
    transcriptions by character overlap ratio, preventing them from being
    incorrectly matched to garbled cues in Round 2 (wide-window retry).

    Args:
        whisper_text: whisper segment text
        left_text: known text of the clean cue before the garbled cluster
        right_text: known text of the clean cue after the garbled cluster
        threshold: minimum character overlap ratio to classify as context

    Returns:
        True if whisper_text is likely a transcription of left or right context
    """
    if not left_text and not right_text:
        return False
    wh_chars = set(whisper_text.replace(' ', ''))
    if not wh_chars:
        return False
    for ctx_text in (left_text, right_text):
        if not ctx_text:
            continue
        ctx_chars = set(ctx_text.replace(' ', ''))
        if not ctx_chars:
            continue
        overlap = len(wh_chars & ctx_chars) / len(wh_chars)
        if overlap >= threshold:
            return True
    return False


def build_clusters(cues):
    """Cluster garbled cues by proximity, expand to clean cue boundaries."""
    garbled_cues = [c for c in cues if c.get('is_garbled')]
    if not garbled_cues:
        return []

    groups, current = [], [garbled_cues[0]]
    for g in garbled_cues[1:]:
        if g['start_s'] - current[-1]['start_s'] <= MAX_CLUSTER_GAP:
            current.append(g)
        else:
            groups.append(current)
            current = [g]
    groups.append(current)

    clusters = []
    for g_group in groups:
        first, last = g_group[0], g_group[-1]
        left_idx = next((k for k, c in enumerate(cues) if c['start'] == first['start']), 0)
        right_idx = next((k for k, c in enumerate(cues) if c['start'] == last['start']), 0)

        left = next((cues[k] for k in range(left_idx - 1, -1, -1)
                     if not cues[k].get('is_garbled') and cues[k]['text']), None)
        right = next((cues[k] for k in range(right_idx + 1, len(cues))
                      if not cues[k].get('is_garbled') and cues[k]['text']), None)

        # Expand to include adjacent clean cues as acoustic context.
        # Using left['start_s'] (not left['end_s']) means Whisper hears
        # the full preceding sentence before attempting the garbled one.
        ss = left['start_s'] if left else max(0, first['start_s'] - GAP_SEC)
        es = right['end_s'] if right else last['end_s'] + GAP_SEC
        ss, es = min(ss, first['start_s']), max(es, last['end_s'])

        clusters.append({
            'ss': ss, 'es': es, 'dur': es - ss,
            'garbled': g_group,
            # Full text stored for _is_context_text() filtering in match_whisper_to_cues
            'left_text': left['text'] if left else '',
            'right_text': right['text'] if right else '',
        })
    return clusters


def match_whisper_to_cues(whisper_segs, cluster, offset):
    """Match whisper segments to garbled cues in a cluster."""
    fixes, covered = [], set()
    cl = cluster['garbled']
    ss = cluster['ss']

    # Round 1: interval overlap
    for wh in whisper_segs:
        wh_abs_s = ss + (wh['start_s'] - offset)
        wh_abs_e = ss + (wh.get('end_s', wh['start_s'] + 8) - offset)
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 8
        matched = []
        for g in cl:
            if g['start_s'] <= wh_abs_e + 1 and wh_abs_s - 1 <= g['end_s']:
                matched.append(g)
                covered.add(g['start'])
        if matched:
            fixes.append({
                'start': matched[0]['start'], 'end': matched[-1]['end'],
                'original': ' | '.join(m['text'][:30] for m in matched),
                'replacement': wh['text'],
                'confidence': 'high', 'model': 'tier1',
                'lines': [m.get('line') for m in matched if m.get('line')],
                # Whisper confidence metadata for AI review (Layer 2.5)
                'avg_logprob': wh.get('avg_logprob'),
                'no_speech_prob': wh.get('no_speech_prob'),
                'compression_ratio': wh.get('compression_ratio'),
            })

    # Round 2: ±3s wide window for missed cues
    for wh in whisper_segs:
        # Skip whisper segments that are transcribing known context cues
        # (the clean left/right sentences we added for acoustic context).
        # Without this guard, a context transcription could be incorrectly
        # matched to a garbled cue when Whisper fails to transcribe it.
        if _is_context_text(wh['text'], cluster.get('left_text', ''),
                            cluster.get('right_text', '')):
            continue
        wh_abs_s = ss + (wh['start_s'] - offset) - 3
        wh_abs_e = ss + (wh.get('end_s', wh['start_s'] + 8) - offset) + 3
        if wh_abs_e <= wh_abs_s:
            wh_abs_e = wh_abs_s + 14
        for g in cl:
            if g['start'] in covered:
                continue
            if g['start_s'] <= wh_abs_e + 1 and wh_abs_s - 1 <= g['end_s']:
                fixes.append({
                    'start': g['start'], 'end': g['end'],
                    'original': g['text'], 'replacement': wh['text'],
                    'confidence': 'retry', 'model': 'tier1',
                    'lines': [g.get('line')] if g.get('line') else [],
                    # Whisper confidence metadata for AI review (Layer 2.5)
                    'avg_logprob': wh.get('avg_logprob'),
                    'no_speech_prob': wh.get('no_speech_prob'),
                    'compression_ratio': wh.get('compression_ratio'),
                })
                covered.add(g['start'])

    # Uncovered
    for g in cl:
        if g['start'] not in covered:
            fixes.append({
                'start': g['start'], 'end': g['end'],
                'original': g['text'], 'replacement': None,
                'confidence': 'none', 'model': 'tier1',
                'lines': [g.get('line')] if g.get('line') else [],
                'avg_logprob': None, 'no_speech_prob': None, 'compression_ratio': None,
            })

    return fixes, covered


def run_tier1(video_path, cues, clusters, whisper_cli, model, language, tmpdir,
              separate_vocals_flag=False):
    """Tier 1: extract garbled segments → concatenate → one Whisper call → map back.

    Instead of running Whisper per-cluster (N model loads = slow), all cluster
    audio is extracted, concatenated with silence gaps, and Whisper runs once.
    Results are then mapped back to original timecodes using cumulative offsets.
    """
    if not clusters:
        return [], []

    all_fixes = []
    all_covered = set()
    seg_paths = []
    cluster_offsets = []  # (cluster_idx, start_in_combined, original_ss)

    # ── Step 1: Extract audio for all clusters ──
    print(f'[Tier1] Extracting {len(clusters)} segments ...', file=sys.stderr)
    for i, cluster in enumerate(clusters):
        ss, es = cluster['ss'], cluster['es']
        seg_audio = os.path.join(tmpdir, f'seg_{i:03d}.wav')
        extract_audio_wav(video_path, seg_audio, ss=ss, duration=es - ss)
        seg_paths.append((i, seg_audio, cluster))

    # ── Step 2: Concatenate into one WAV ──
    combined_wav = os.path.join(tmpdir, 'combined.wav')
    silence_s = 2.0
    offsets, total_dur = _concat_wavs(seg_paths, combined_wav, silence_s=silence_s)

    print(f'[Tier1] Combined: {total_dur:.1f}s ({len(seg_paths)} segments + silence)',
          file=sys.stderr)

    # ── Step 3: Optional demucs on combined audio (once, not per-segment) ──
    whisper_input = combined_wav
    if separate_vocals_flag:
        print(f'[Tier1] Demucs vocal separation (once on combined audio) ...', file=sys.stderr)
        vocals = separate_vocals(combined_wav, output_dir=tmpdir)
        if vocals != combined_wav:
            whisper_input = vocals

    # ── Step 4: One Whisper call ──
    segs = run_whisper(whisper_input, whisper_cli, model, language)
    if not segs:
        print(f'[Tier1] ⚠ Whisper produced no output', file=sys.stderr)
        unmatched = [c for c in cues if c.get('is_garbled')]
        return [], unmatched

    print(f'[Tier1] Whisper returned {len(segs)} segments', file=sys.stderr)

    # ── Step 5: Map Whisper segments back to original clusters ──
    # offsets = [(cluster_idx, combined_start, combined_end, original_ss), ...]
    for ci, combined_start, combined_end, orig_ss in offsets:
        cluster = clusters[ci]
        # Find Whisper segments that fall within this cluster's time range in combined audio
        cluster_segs = []
        for s in segs:
            s_mid = (s['start_s'] + s['end_s']) / 2
            if combined_start <= s_mid <= combined_end:
                # Shift to cluster-local time (match_whisper_to_cues adds cluster['ss'])
                cluster_segs.append({
                    **s,
                    'start_s': s['start_s'] - combined_start,
                    'end_s': s['end_s'] - combined_start,
                })

        if cluster_segs:
            # match_whisper_to_cues adds cluster['ss'] internally, so segs
            # must use time offsets relative to cluster audio start.
            fixes, covered = match_whisper_to_cues(cluster_segs, cluster, offset=0)
            all_fixes.extend(fixes)
            all_covered.update(covered)
            fixed = sum(1 for f in fixes if f['confidence'] in ('high', 'retry'))
            print(f'  [cluster {ci}] {fixed}/{len(fixes)} fixed from '
                  f'{len(cluster_segs)} whisper segs', file=sys.stderr)
        else:
            print(f'  [cluster {ci}] no whisper segments matched', file=sys.stderr)

    # Collect unmatched cues
    unmatched = []
    for c in cues:
        if c.get('is_garbled') and c['start'] not in all_covered:
            unmatched.append(c)

    return all_fixes, unmatched


def _concat_wavs(seg_paths, output_path, silence_s=2.0, sample_rate=16000):
    """Concatenate WAV files with silence gaps. Returns offsets and total duration.

    Args:
        seg_paths: [(cluster_idx, wav_path, cluster_dict), ...]
        output_path: where to write the combined WAV
        silence_s: seconds of silence between segments
        sample_rate: expected sample rate

    Returns:
        offsets: [(cluster_idx, start_s, end_s, original_ss), ...]
        total_dur: total duration in seconds
    """
    import wave
    import struct

    all_frames = []
    offsets = []
    current_pos = 0.0

    for ci, wav_path, cluster in seg_paths:
        with wave.open(wav_path, 'rb') as wf:
            rate = wf.getframerate()
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())

        dur = len(frames) / (rate * nchannels * sampwidth)
        offsets.append((ci, current_pos, current_pos + dur, cluster['ss']))

        all_frames.append(frames)
        current_pos += dur

        # Add silence gap (except after last segment)
        if ci != seg_paths[-1][0]:
            silence_frames = int(silence_s * rate * nchannels * sampwidth)
            all_frames.append(b'\x00' * silence_frames)
            current_pos += silence_s

    # Write combined WAV (use params from first segment)
    with wave.open(seg_paths[0][1], 'rb') as ref:
        ref_rate = ref.getframerate()
        ref_nch = ref.getnchannels()
        ref_sw = ref.getsampwidth()

    with wave.open(output_path, 'wb') as out:
        out.setnchannels(ref_nch)
        out.setsampwidth(ref_sw)
        out.setframerate(ref_rate)
        out.writeframes(b''.join(all_frames))

    total_dur = current_pos
    return offsets, total_dur


# ═══════════════════════════════════════════════════════════════
# Tier 2: full-episode fix
# ═══════════════════════════════════════════════════════════════

def align_and_fix(cues, whisper_segs, save_transcript_to: str = None, target_lang='ja'):
    """Align whisper segments to SRT cues by time overlap.

    Args:
        cues: parsed SRT cues
        whisper_segs: Whisper transcription segments [{start_s, end_s, text, ...}]
        save_transcript_to: if set, write whisper_segs as JSON for AI context
    """
    fixes = []
    stats = {'fixed': 0, 'unmatched': 0, 'kept': 0}

    # ── Save full Whisper transcript for AI fragment context (Level 2) ──
    if save_transcript_to:
        try:
            slim_segs = [{'start_s': round(s.get('start_s', 0), 1),
                          'text': s.get('text', '')}
                         for s in whisper_segs if s.get('text', '').strip()]
            os.makedirs(os.path.dirname(save_transcript_to), exist_ok=True)
            with open(save_transcript_to, 'w', encoding='utf-8') as fh:
                json.dump(slim_segs, fh, ensure_ascii=False)
        except Exception as e:
            print(f'  [align] Failed to save whisper transcript: {e}',
                  file=sys.stderr)

    for c in cues:
        if not c.get('is_garbled'):
            stats['kept'] += 1
            continue

        best_overlap = 0
        best_seg = None
        for wh in whisper_segs:
            overlap_start = max(c['start_s'], wh['start_s'])
            overlap_end = min(c['end_s'], wh['end_s'])
            overlap = overlap_end - overlap_start
            if overlap > 0:
                cue_dur = c['end_s'] - c['start_s']
                if cue_dur > 0:
                    ratio = overlap / cue_dur
                    if ratio > best_overlap:
                        best_overlap = ratio
                        best_seg = wh

        if best_seg and best_overlap >= 0.3 and is_valid_subtitle_text(best_seg['text'], target_lang):
            fixes.append({
                'start': c['start'], 'end': c['end'],
                'original': c['text'][:80],
                'replacement': best_seg['text'][:80],
                'confidence': 'high' if best_overlap >= 0.5 else 'retry',
                'model': 'tier2',
                'lines': [c.get('line')] if c.get('line') else [],
                # Whisper confidence metadata for AI review (Layer 2.5)
                'avg_logprob': best_seg.get('avg_logprob'),
                'no_speech_prob': best_seg.get('no_speech_prob'),
                'compression_ratio': best_seg.get('compression_ratio'),
            })
            stats['fixed'] += 1
        else:
            fixes.append({
                'start': c['start'], 'end': c['end'],
                'original': c['text'],
                'replacement': None,
                'confidence': 'none',
                'model': 'tier2',
                'lines': [c.get('line')] if c.get('line') else [],
                'avg_logprob': None, 'no_speech_prob': None, 'compression_ratio': None,
            })
            stats['unmatched'] += 1

    return fixes, stats


def run_tier2(video_path, cues, whisper_cli, model, language, tmpdir,
              separate_vocals_flag=False, save_transcript_to: str = None):
    """Tier 2: full episode transcription + alignment.

    Args:
        save_transcript_to: if set, save slim whisper_segs JSON for AI context
    """
    print('  [Tier 2] Full episode transcription ...', file=sys.stderr)

    full_audio = os.path.join(tmpdir, 'full.wav')
    extract_audio_wav(video_path, full_audio)

    # Optional demucs vocal separation
    whisper_input = full_audio
    if separate_vocals_flag:
        vocals = separate_vocals(full_audio, output_dir=tmpdir)
        if vocals != full_audio:
            whisper_input = vocals

    whisper_segs = run_whisper(whisper_input, whisper_cli, model, language)

    if not whisper_segs:
        print('  ⚠ Whisper 无输出', file=sys.stderr)
        return [], {'fixed': 0, 'unmatched': len([c for c in cues if c.get('is_garbled')]), 'kept': 0}

    print(f'  {len(whisper_segs)} segments', file=sys.stderr)
    return align_and_fix(cues, whisper_segs, save_transcript_to=save_transcript_to)


# ═══════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Whisper pipeline — VAD clean → segment-first → auto-upgrade to full-episode')
    parser.add_argument('video', help='Video file path')
    parser.add_argument('srt', help='SRT file path')
    parser.add_argument('--whisper-cli', required=True, help='Path to whisper-cli.exe')
    parser.add_argument('--model', required=True, help='Path to whisper model')
    parser.add_argument('--retry-model', help='Fallback model for retry')
    parser.add_argument('--language', '-l', default='ja', help='Whisper language (default: ja)')
    parser.add_argument('--output', '-o', help='Output fixes.json path')
    parser.add_argument('--dry-run', action='store_true', help='Preview only')
    parser.add_argument('--separate-vocals', action='store_true',
                        help='Use demucs to separate vocals before Whisper')
    parser.add_argument('--force-tier2', action='store_true',
                        help='Skip Tier 1, go directly to full-episode')
    parser.add_argument('--no-vad-clean', action='store_true',
                        help='Skip VAD pre-scan (keep all cues)')
    parser.add_argument('--detect-missing-dialogue', action='store_true',
                        help='Detect speech without subtitles and add placeholder cues')
    parser.add_argument('--missing-dialogue-min-gap', type=float, default=3.0,
                        help='Min gap (seconds) for missing dialogue detection (default: 3.0)')
    parser.add_argument('--vad-aggressiveness', type=int, default=2,
                        help='WebRTC VAD aggressiveness 0-3 (default: 2)')
    args = parser.parse_args()

    tmpdir = tempfile.mkdtemp()
    try:
        # ── Step 0: VAD pre-scan — delete non-speech cues ──
        cues = parse_srt(args.srt)
        original_count = len(cues)
        deleted_count = 0

        if not args.no_vad_clean:
            print('[VAD] Extracting full audio for speech detection ...', file=sys.stderr)
            vad_audio = os.path.join(tmpdir, 'vad_full.wav')
            extract_audio_wav(args.video, vad_audio)
            cues, deleted, speech_segs = vad_delete_nonspeech(vad_audio, cues, args.srt)
            deleted_count = len(deleted)
            # Optional: detect speech without subtitles → fix regions
            if args.detect_missing_dialogue:
                regions = build_fix_regions(speech_segs, cues)
                if regions:
                    print(f'[VAD] {len(regions)} fix regions detected '
                          f'(garbled + partial + missing)', file=sys.stderr)
        else:
            print('[VAD] --no-vad-clean: skipping speech detection', file=sys.stderr)

        # ── Step 1: Find garbled cues in remaining ──
        garbled = [c for c in cues if c.get('is_garbled')]
        print(f'[scan] {len(cues)} cues ({deleted_count} deleted by VAD), '
              f'{len(garbled)} garbled', file=sys.stderr)

        if not garbled:
            print(f'✓ All garbled cues deleted by VAD — nothing to fix.', file=sys.stderr)
            if args.output:
                report = {
                    'srt': os.path.basename(args.srt),
                    'total_cues': original_count,
                    'deleted_by_vad': deleted_count,
                    'garbled_cues': 0, 'tier': 0, 'fixed': 0, 'unmatched': 0,
                    'fixes': [],
                }
                with open(args.output, 'w', encoding='utf-8') as f:
                    json.dump(report, f, ensure_ascii=False, indent=2)
                print(f'→ {args.output}', file=sys.stderr)
            return

        # ── Step 2: Whisper Tier 1/2 ──
        if args.force_tier2 or len(garbled) > UPGRADE_THRESHOLD:
            tier = 2
            print(f'[tier] {len(garbled)} fragments > {UPGRADE_THRESHOLD} → Tier 2 (full-episode)',
                  file=sys.stderr)
            fixes, stats = run_tier2(args.video, cues, args.whisper_cli, args.model,
                                     args.language, tmpdir,
                                     separate_vocals_flag=args.separate_vocals)
            unmatched = list(garbled)
        else:
            tier = 1
            print(f'[tier] {len(garbled)} fragments ≤ {UPGRADE_THRESHOLD} → Tier 1 (segment)',
                  file=sys.stderr)
            clusters = build_clusters(cues)
            print(f'  {len(clusters)} clusters', file=sys.stderr)
            if not clusters:
                print('⚠ No clusters built — skipping Whisper.', file=sys.stderr)
                fixes, unmatched = [], []
            else:
                fixes, unmatched = run_tier1(args.video, cues, clusters, args.whisper_cli,
                                             args.model, args.language, tmpdir,
                                             separate_vocals_flag=args.separate_vocals)

            # Try retry model for remaining unmatched
            if unmatched and args.retry_model:
                print(f'\n[retry] {len(unmatched)} unmatched → retry with backup model ...',
                      file=sys.stderr)
                retry_clusters = build_clusters(
                    [{**c, 'is_garbled': True} if c['start'] in {u['start'] for u in unmatched}
                     else c for c in cues]
                )
                if retry_clusters:
                    retry_fixes, _ = run_tier1(args.video, cues, retry_clusters,
                                               args.whisper_cli, args.retry_model,
                                               args.language, tmpdir,
                                               separate_vocals_flag=args.separate_vocals)
                    fixes.extend([f for f in retry_fixes if f['confidence'] != 'none'])

            # Auto-upgrade if too many unmatched after Tier 1
            final_unmatched = [f for f in fixes if f['confidence'] == 'none']
            if len(final_unmatched) > UPGRADE_THRESHOLD:
                print(f'\n[tier] {len(final_unmatched)} still unmatched → upgrading to Tier 2 ...',
                      file=sys.stderr)
                t2_fixes, stats = run_tier2(args.video, cues, args.whisper_cli, args.model,
                                            args.language, tmpdir,
                                            separate_vocals_flag=args.separate_vocals)
                t1_success = {f['start'] for f in fixes if f['confidence'] != 'none'}
                merged = [f for f in fixes if f['confidence'] != 'none']
                merged.extend([f for f in t2_fixes if f['start'] not in t1_success])
                fixes = merged

        # ── Stats ──
        fixed = sum(1 for f in fixes if f['confidence'] in ('high', 'retry'))
        failed = sum(1 for f in fixes if f['confidence'] == 'none')
        print(f'\n[result] VAD deleted: {deleted_count}, '
              f'Whisper fixed: {fixed}, unmatched: {failed}', file=sys.stderr)

        # ── Output ──
        if args.dry_run:
            print('\n[DRY-RUN] Preview:', file=sys.stderr)
            for f in [fx for fx in fixes if fx['confidence'] != 'none'][:10]:
                print(f'  [{f["start"]}] {f["original"][:50]} → {f["replacement"][:50]}',
                      file=sys.stderr)
        else:
            applied = apply_fixes_to_srt(args.srt, fixes) if not args.dry_run else 0
            print(f'  Applied: {applied} cues written to SRT', file=sys.stderr)

        if args.output:
            report = {
                'srt': os.path.basename(args.srt),
                'total_cues': original_count,
                'deleted_by_vad': deleted_count,
                'garbled_cues': len(garbled),
                'tier': tier, 'fixed': fixed, 'unmatched': failed,
                'fixes': fixes,
            }
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f'→ {args.output}', file=sys.stderr)

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
