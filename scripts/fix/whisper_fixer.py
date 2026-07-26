#!/usr/bin/env python3
"""Whisper ASR correction pipeline — VAD → cluster → Tier1/2 → triage.

Operates on a SubtitleSession.  Returns structured WhisperResult data
for the orchestrator to write to SRT and report.  Writes temp files
directly (VAD cache, Whisper transcript JSON from Tier 2) but NEVER
writes SRT or the problem report.
"""

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field

import lib._path  # noqa: F401

from lib.whisper_utils import (
    setup_windows_utf8, parse_srt, write_srt, apply_fixes_to_srt,
    run_whisper, extract_audio_wav, meaningful_char_count,
    is_length_anomaly, is_valid_subtitle_text, looks_like_plausible_text,
    format_tc, to_seconds,
)
setup_windows_utf8()

from fix.subtitle_session import SubtitleSession


# ═══════════════════════════════════════════════════════════════
# WhisperResult — structured return type (replaces FixReport)
# ═══════════════════════════════════════════════════════════════

@dataclass
class WhisperResult:
    """Structured result from Whisper fix pipeline — no SRT writes here.

    The orchestrator reads these fields and applies them to SRT + report:
      - auto_keep_fixes → write to SRT (apply_fixes_to_srt), report step 2 ✅
      - ai_fragments    → build ai_fragments JSON, report step 2.5 ⬜
      - auto_cuts       → delete from SRT, report step 2 🗑️
      - new_cues        → insert into SRT (missing_sub only)
    """
    source: str = 'whisper'
    applied: int = 0          # auto-keep count
    ai_review: int = 0        # AI fragment count
    deleted: int = 0          # auto-cut + VAD-deleted count
    tier: int = 0
    auto_keep_fixes: list = field(default_factory=list)
    ai_fragments: list = field(default_factory=list)
    auto_cuts: list = field(default_factory=list)
    new_cues: list = field(default_factory=list)   # for new cues inserted into gaps
    cues_to_clear: list = field(default_factory=list)  # v5.3: overlapped cues to delete
    details: list = field(default_factory=list)

    @classmethod
    def empty(cls, source: str = 'whisper', deleted: int = 0,
              details: list = None):
        return cls(source=source, deleted=deleted, details=details or [])


# ═══════════════════════════════════════════════════════════════
# Module-level helpers
# ═══════════════════════════════════════════════════════════════

def _apply_vad_clean_from_cache(speech_segs, cues, srt_path, target_lang='ja'):
    """Apply VAD clean using cached speech timeline from Phase 1 scan."""
    from fix.whisper_pipeline import cue_overlaps_speech, is_non_dialogue_marker
    from lib.whisper_utils import looks_like_plausible_text, write_srt
    kept, deleted = [], []
    for c in cues:
        text = c['text'].strip()
        if is_non_dialogue_marker(text, target_lang):
            deleted.append(c)
            continue
        has_speech = cue_overlaps_speech(c, speech_segs, min_overlap_s=0.0)
        if not has_speech:
            if looks_like_plausible_text(text, target_lang):
                kept.append(c)
                continue
            if c['end_s'] - c['start_s'] < 3.0:
                deleted.append(c)
                continue
        kept.append(c)
    if deleted:
        write_srt(srt_path, kept)
        print(f'[VAD cache] Deleted {len(deleted)} cues (Phase 1 timeline)',
              file=sys.stderr)
    return kept, deleted, speech_segs


def _translate_whisper_replacements(all_items: list, target_lang: str):
    """Translate Whisper replacement text from Japanese to target language.

    Only activates when target_lang ≠ 'ja'.  Uses Baidu Translate API if
    credentials are available; degrades gracefully otherwise.
    """
    if target_lang == 'ja':
        return

    to_translate = [f for f in all_items
                    if f.get('replacement', '').strip()]

    if not to_translate:
        return

    appid = None
    secret = None
    endpoint = None
    try:
        from fix.translate_srt import baidu_translate, load_credentials
        appid, secret, endpoint = load_credentials()
    except Exception:
        pass

    if not appid or not secret:
        print(f'[translate] Baidu credentials not found — '
              f'keeping Japanese originals (AI will translate '
              f'{len(to_translate)} items)',
              file=sys.stderr)
        print(f'[translate] Set BAIDU_APPID + BAIDU_SECRET or '
              f'~/.baidu_translate to enable auto-translation.',
              file=sys.stderr)
        return

    print(f'[translate] Baidu: translating {len(to_translate)} Whisper '
          f'outputs ja → {target_lang} ...', file=sys.stderr)

    translated_count = 0
    failed_count = 0
    for f in to_translate:
        jp_text = f['replacement'].strip()
        try:
            from fix.translate_srt import baidu_translate
            translated = baidu_translate(
                jp_text, appid, secret, source='ja', target=target_lang,
                endpoint=endpoint)
            if translated and translated != jp_text:
                f['whisper_original_ja'] = jp_text
                f['replacement'] = translated
                translated_count += 1
            else:
                failed_count += 1
        except Exception:
            failed_count += 1

    print(f'[translate] Done: {translated_count} translated, '
          f'{failed_count} failed (kept original)',
          file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# WhisperFixer
# ═══════════════════════════════════════════════════════════════

class WhisperFixer:
    """VAD clean → cluster → Tier1/2 → triage (auto-keep / AI / auto-cut).

    Does NOT write SRT or report.  Returns WhisperResult with categorized
    items.  The orchestrator is responsible for applying cuts/keep-fixes
    to SRT, building AI fragments JSON, and updating the report.

    Writes temp files: VAD cache (via session.save_speech_segs) and
    Whisper full-ep transcript JSON (via run_tier2's save_transcript_to).
    """

    def __init__(self, session: SubtitleSession):
        self._session = session

    # ── Convenience properties ──

    @property
    def _episode(self): return self._session.episode

    @property
    def _srt_path(self): return self._session.srt_path

    @property
    def _video_path(self): return self._session.video_path

    @property
    def _srt_dir(self): return self._session.srt_dir

    @property
    def _temp_dir(self): return self._session.temp_dir

    @property
    def _target_lang(self): return self._session.target_lang

    @property
    def _whisper_cli(self): return self._session.whisper_cli

    @property
    def _model(self): return self._session.model

    @property
    def _retry_model(self): return self._session.retry_model

    # ═══════════════════════════════════════════════════════════
    # Source 2: Whisper
    # ═══════════════════════════════════════════════════════════

    def fix_by_whisper(self, *, separate_vocals: bool = True,
                       force_tier2: bool = False,
                       skip_vad_clean: bool = False) -> WhisperResult:
        """Run Whisper auto-fix pipeline (unified VAD-driven).

        1. VAD clean: delete non-speech cues
        2. Build unified fix regions from VAD speech segments
           → single function replaces build_clusters() + find_missing_subtitle_gaps()
        3. Convert regions to cluster format → Tier 1/2 Whisper
        4. Triage: auto-keep ✅ / AI fragments 🤖 / auto-cut 🗑️

        Does NOT write SRT or report.  Returns WhisperResult.
        """
        if not self._video_path:
            msg = (f'[whisper] {self._episode}: No video file found.\n'
                   f'  Searched: video_dir={self._session.video_dir or "(not set)"}, '
                   f'project/video, project/videos.\n'
                   f'  Fix: pass --video-dir, or add the path to CLAUDE.md '
                   f'under 「项目路径」with 「视频」in the description.')
            print(msg, file=sys.stderr)
            return WhisperResult.empty(source='whisper',
                                       details=['No video file found'])

        if not self._srt_path:
            msg = (f'[whisper] {self._episode}: No SRT file found in '
                   f'{self._srt_dir}')
            print(msg, file=sys.stderr)
            return WhisperResult.empty(source='whisper',
                                       details=['No SRT file to fix'])

        cues = parse_srt(self._srt_path, mark_garbled=True,
                         target_lang=self._target_lang)
        original_count = len(cues)
        garbled = [c for c in cues if c.get('is_garbled')]

        if not garbled:
            print(f'[whisper] {self._episode}: all clean, nothing to fix',
                  file=sys.stderr)
            return WhisperResult.empty(source='whisper')

        print(f'[whisper] {self._episode}: {len(garbled)}/{original_count} '
              f'garbled cues', file=sys.stderr)

        tmpdir = tempfile.mkdtemp()
        try:
            deleted_count = 0

            # Step 1: VAD clean — try cached speech timeline first
            speech_segs = []
            if not skip_vad_clean:
                from fix.whisper_pipeline import vad_delete_nonspeech

                cached_segs = self._session.load_speech_segs()

                if cached_segs:
                    print(f'[whisper] Using cached VAD from Phase 1 '
                          f'({len(cached_segs)} speech segments)',
                          file=sys.stderr)
                    cues, deleted, speech_segs = _apply_vad_clean_from_cache(
                        cached_segs, cues, self._srt_path,
                        target_lang=self._target_lang)
                    deleted_count = len(deleted)
                else:
                    vad_audio = os.path.join(tmpdir, 'vad_full.wav')
                    try:
                        extract_audio_wav(self._video_path, vad_audio)
                        cues, deleted, speech_segs = vad_delete_nonspeech(
                            vad_audio, cues, self._srt_path,
                            target_lang=self._target_lang)
                        deleted_count = len(deleted)
                        self._session.save_speech_segs(speech_segs)
                    except Exception as e:
                        print(f'[whisper] VAD audio extraction failed: {e}',
                              file=sys.stderr)
                        print(f'[whisper] Continuing without VAD clean',
                              file=sys.stderr)

            # ── Build unified fix regions from VAD speech segments ──
            # v5.3: Single function replaces build_clusters() +
            # find_missing_subtitle_gaps() + add_placeholder_cues().
            # VAD speech segments are the ground truth — all affected cues
            # (garbled, partial-overlap, missing) are handled in one pass.
            from fix.whisper_pipeline import (
                build_fix_regions, run_tier1, run_tier2,
                UPGRADE_THRESHOLD,
            )

            regions = build_fix_regions(speech_segs, cues,
                                         target_lang=self._target_lang)

            if not regions:
                print(f'[whisper] {self._episode}: no fix regions found',
                      file=sys.stderr)
                return WhisperResult(source='whisper',
                                     deleted=deleted_count)

            # Convert regions → cluster format for Tier 1/2 compatibility.
            # Context cues expand the audio range for better Whisper accuracy.
            from fix.whisper_pipeline import regions_to_clusters
            clusters = regions_to_clusters(regions)

            total_garbled = sum(len(r['garbled_cues']) for r in regions)
            total_cues_to_clear = sum(len(r['cues_to_clear']) for r in regions)
            print(f'[whisper] {self._episode}: {len(regions)} fix regions '
                  f'({total_garbled} garbled + '
                  f'{total_cues_to_clear - total_garbled} partial/missing)',
                  file=sys.stderr)

            if force_tier2 or total_garbled > UPGRADE_THRESHOLD:
                tier = 2
                print(f'[whisper] {total_garbled} garbled > '
                      f'{UPGRADE_THRESHOLD} → Tier 2 (full-episode)',
                      file=sys.stderr)
                fixes, stats = run_tier2(
                    self._video_path, cues,
                    self._whisper_cli, self._model,
                    self._target_lang, tmpdir,
                    separate_vocals_flag=separate_vocals,
                    save_transcript_to=os.path.join(
                        self._temp_dir,
                        f'whisper_full_{self._episode}.json'),
                )
            else:
                tier = 1
                if not clusters:
                    fixes, _ = [], []
                else:
                    print(f'[whisper] Tier 1: {len(clusters)} clusters '
                          f'from {len(regions)} regions',
                          file=sys.stderr)
                    fixes, unmatched_cues = run_tier1(
                        self._video_path, cues, clusters,
                        self._whisper_cli, self._model,
                        self._target_lang, tmpdir,
                        separate_vocals_flag=separate_vocals,
                    )

                    # Retry with backup model
                    if unmatched_cues and self._retry_model:
                        print(f'[whisper] {len(unmatched_cues)} unmatched → '
                              f'retry with backup model', file=sys.stderr)
                        retry_clusters = regions_to_clusters(
                            build_fix_regions(speech_segs, cues,
                                              target_lang=self._target_lang))
                        if retry_clusters:
                            retry_fixes, _ = run_tier1(
                                self._video_path, cues, retry_clusters,
                                self._whisper_cli, self._retry_model,
                                self._target_lang, tmpdir,
                                separate_vocals_flag=separate_vocals,
                            )
                            fixes.extend([f for f in retry_fixes
                                         if f['confidence'] != 'none'])

                    # Auto-upgrade to Tier 2
                    still_unmatched = [f for f in fixes
                                      if f['confidence'] == 'none']
                    if len(still_unmatched) > UPGRADE_THRESHOLD:
                        print(f'[whisper] {len(still_unmatched)} still '
                              f'unmatched → Tier 2', file=sys.stderr)
                        t2_fixes, _ = run_tier2(
                            self._video_path, cues,
                            self._whisper_cli, self._model,
                            self._target_lang, tmpdir,
                            separate_vocals_flag=separate_vocals,
                            save_transcript_to=os.path.join(
                                self._temp_dir,
                                f'whisper_full_{self._episode}.json'),
                        )
                        t1_success = {f['start'] for f in fixes
                                      if f['confidence'] != 'none'}
                        merged = [f for f in fixes
                                  if f['confidence'] != 'none']
                        merged.extend([f for f in t2_fixes
                                       if f['start'] not in t1_success])
                        fixes = merged
                        tier = 2

            # Record cues_to_clear for orchestrator deletion
            cues_to_clear = _collect_cues_to_clear(regions)

            # Step 3: Triage — classify BEFORE returning
            result = WhisperResult(source='whisper', tier=tier,
                                   deleted=deleted_count)

            auto_keep = []
            ai_fragments = []
            auto_cut = []

            all_items = list(fixes)

            # Fallback: if Whisper produced no fixes, evaluate garbled directly
            if not fixes and garbled:
                print(f'[whisper] No Whisper output — evaluating '
                      f'{len(garbled)} garbled cue(s) directly',
                      file=sys.stderr)
                for g in garbled:
                    all_items.append({
                        'start': g['start'], 'end': g['end'],
                        'original': g['text'], 'replacement': None,
                        'confidence': 'none', 'model': 'tier1',
                    })

            # Translate Whisper output ja → target_lang
            _translate_whisper_replacements(all_items, self._target_lang)

            for f in all_items:
                eval_text = (f.get('replacement')
                             or f.get('original')
                             or '').strip()

                # ① No meaningful chars → auto-cut
                if meaningful_char_count(eval_text, self._target_lang) < 2:
                    auto_cut.append(f)
                    continue

                # ② Plausible text → auto-keep (unless hallucination)
                if looks_like_plausible_text(eval_text, self._target_lang):
                    original = f.get('original', '')
                    if (f.get('replacement')
                            and meaningful_char_count(original,
                                                      self._target_lang) >= 2
                            and is_length_anomaly(original, eval_text)):
                        ai_fragments.append(f)
                    else:
                        auto_keep.append(f)
                    continue

                # ③ Has target-language content but corrupt → AI completion
                ai_fragments.append(f)

            # ── Populate result (NO SRT writes!) ──
            result.auto_keep_fixes = auto_keep
            result.ai_fragments = ai_fragments
            result.auto_cuts = auto_cut
            result.applied = len(auto_keep)
            result.ai_review = len(ai_fragments)
            # Add auto-cut count to deleted
            # (actual deletion happens in orchestrator)
            result.deleted += len(auto_cut)
            # v5.3: cues_to_clear from fix regions (overlapped cues to delete)
            result.cues_to_clear = cues_to_clear

            for f in auto_cut:
                print(f'  [cut] {f["start"]}: '
                      f'"{f.get("original", "")[:60]}"', file=sys.stderr)

            print(f'[whisper] {result.applied} auto-keep, '
                  f'{result.ai_review} → AI, '
                  f'{len(auto_cut)} auto-cut, '
                  f'{result.deleted} total deleted', file=sys.stderr)

            return result

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

def _collect_cues_to_clear(regions):
    """Collect all cues_to_clear from fix regions, deduplicated by start time."""
    seen = set()
    cues = []
    for region in regions:
        for c in region.get('cues_to_clear', []):
            if c['start'] not in seen:
                seen.add(c['start'])
                cues.append(c)
    return cues
