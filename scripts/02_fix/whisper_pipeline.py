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

_script_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_script_dir)

if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from lib.whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds,
    parse_srt, write_srt, apply_fixes_to_srt, run_whisper,
    extract_audio_wav, is_valid_japanese,
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

    wf = wave.open(audio_path, 'rb')
    rate = wf.getframerate()
    nchannels = wf.getnchannels()
    sampwidth = wf.getsampwidth()

    if rate != 16000 or nchannels != 1 or sampwidth != 2:
        wf.close()
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

    wf.close()
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


# ── Content-based non-dialogue markers ──
# These are editorial markers (sound effects, music cues, audience reactions)
# that should ALWAYS be deleted regardless of speech overlap.
# They are NOT dialogue — they're production notes for dubbing/editing.
NON_DIALOGUE_PATTERNS = [
    r'^\[音楽\]$',      # music
    r'^\[拍手\]$',       # applause
    r'^\[笑い\]$',       # laughter
    r'^\[歓声\]$',       # cheers
    r'^\[悲鳴\]$',       # scream
    r'^\[鳴き声\]$',     # animal cry
    r'^\[足音\]$',       # footsteps
    r'^\[効果音\]$',     # sound effect
    r'^\[鐘\]$',         # bell
    r'^\[笛\]$',         # whistle
    r'^\[雷\]$',         # thunder
    r'^\[風\]$',         # wind
    r'^\[波\]$',         # waves
    r'^\[雨\]$',         # rain
    r'^\[爆発\]$',       # explosion
    r'^\[銃声\]$',       # gunshot
    r'^\[車\]$',         # car
    r'^\[飛行機\]$',     # airplane
    r'^\[電話\]$',       # telephone
    r'^\[ベル\]$',       # bell (en)
    r'^\[チャイム\]$',   # chime
    r'^\[ノック\]$',     # knock
    r'^\[ドア\]$',       # door
    r'^\[SE\]$',         # sound effect (en)
    r'^\[BGM\]$',        # background music (en)
    r'^\[ざわめき\]$',   # murmur
    r'^\[どよめき\]$',   # stir
]
NON_DIALOGUE_RE = re.compile('|'.join(NON_DIALOGUE_PATTERNS))


def is_non_dialogue_marker(text):
    """Check if text is a known non-dialogue editorial marker."""
    return bool(NON_DIALOGUE_RE.match(text.strip()))


def vad_delete_nonspeech(audio_path, cues, srt_path):
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
        if is_non_dialogue_marker(text):
            deleted.append(c)
            continue

        # Tier B: text cues — delete only if NO speech at all AND short
        has_speech = cue_overlaps_speech(c, speech_segs, min_overlap_s=0.0)
        if not has_speech:
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

    return kept, deleted


# ═══════════════════════════════════════════════════════════════
# Tier 1: segment-based fix
# ═══════════════════════════════════════════════════════════════

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

        ss = left['end_s'] if left else max(0, first['start_s'] - GAP_SEC)
        es = right['start_s'] if right else last['end_s'] + GAP_SEC
        ss, es = min(ss, first['start_s']), max(es, last['end_s'])

        clusters.append({
            'ss': ss, 'es': es, 'dur': es - ss,
            'garbled': g_group,
            'left_text': left['text'][:60] if left else '(无)',
            'right_text': right['text'][:60] if right else '(无)',
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
            })

    # Round 2: ±3s wide window for missed cues
    for wh in whisper_segs:
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
            })

    return fixes, covered


def run_tier1(video_path, cues, clusters, whisper_cli, model, language, tmpdir,
              separate_vocals_flag=False):
    """Tier 1: extract segments around garbled clusters, whisper each, match results."""
    all_fixes = []
    all_covered = set()

    for i, cluster in enumerate(clusters):
        ss, es = cluster['ss'], cluster['es']
        print(f'  [{i+1}/{len(clusters)}] segment {ss:.1f}s–{es:.1f}s '
              f'({len(cluster["garbled"])} cues) ...', file=sys.stderr)

        # Extract audio segment
        seg_audio = os.path.join(tmpdir, f'seg_{i:03d}.wav')
        extract_audio_wav(video_path, seg_audio, ss=ss, duration=es - ss)

        # Optional demucs vocal separation
        whisper_input = seg_audio
        if separate_vocals_flag:
            vocals = separate_vocals(seg_audio, output_dir=tmpdir)
            if vocals != seg_audio:
                whisper_input = vocals

        # Whisper
        segs = run_whisper(whisper_input, whisper_cli, model, language)
        if not segs:
            print(f'    ⚠ no output', file=sys.stderr)
            continue

        # Match
        fixes, covered = match_whisper_to_cues(segs, cluster, offset=segs[0].get('t0', 0))
        all_fixes.extend(fixes)
        all_covered.update(covered)

        fixed = sum(1 for f in fixes if f['confidence'] in ('high', 'retry'))
        print(f'    {fixed}/{len(fixes)} fixed', file=sys.stderr)

    # Collect unmatched cues
    unmatched = []
    for c in cues:
        if c.get('is_garbled') and c['start'] not in all_covered:
            unmatched.append(c)

    return all_fixes, unmatched


# ═══════════════════════════════════════════════════════════════
# Tier 2: full-episode fix
# ═══════════════════════════════════════════════════════════════

def align_and_fix(cues, whisper_segs):
    """Align whisper segments to SRT cues by time overlap."""
    fixes = []
    stats = {'fixed': 0, 'unmatched': 0, 'kept': 0}

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

        if best_seg and best_overlap >= 0.3 and is_valid_japanese(best_seg['text']):
            fixes.append({
                'start': c['start'], 'end': c['end'],
                'original': c['text'][:80],
                'replacement': best_seg['text'][:80],
                'confidence': 'high' if best_overlap >= 0.5 else 'retry',
                'model': 'tier2',
                'lines': [c.get('line')] if c.get('line') else [],
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
            })
            stats['unmatched'] += 1

    return fixes, stats


def run_tier2(video_path, cues, whisper_cli, model, language, tmpdir,
              separate_vocals_flag=False):
    """Tier 2: full episode transcription + alignment."""
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
    return align_and_fix(cues, whisper_segs)


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
            cues, deleted = vad_delete_nonspeech(vad_audio, cues, args.srt)
            deleted_count = len(deleted)
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
