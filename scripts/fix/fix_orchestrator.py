#!/usr/bin/env python3
"""Fix orchestrator — unified error-fix pipeline (Layer 2).

Replaces the split between whisper_pipeline.py (auto-fix) and
extract_review_clips.py (manual review). Three fix sources, one
embed path, cascading priority:

  1. Reference — translated reference SRT comparison (most reliable)
  2. Whisper   — audio transcription with context wrapping
  3. Human     — checklist-based manual correction (last resort)

All corrections go through the same SRT write + report update path.
SRT is the single source of truth; classify_garbled_text guarantees
idempotency across repeated runs.

Usage:
  from fix_orchestrator import Fixer

  fixer = Fixer('EP002', '/path/to/project')
  if not fixer.is_clean():
      result = fixer.run_auto()  # cascade: reference → whisper → review

  # Later, after human fills the checklist:
  Fixer('EP002', project_dir).apply('reports/manual-review/EP002_checklist.md')
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from utils.update_report import upsert_entries, update_entry_status
from dataclasses import dataclass, field
from datetime import datetime
import lib._path  # noqa: F401

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/ — needed for subprocess PYTHONPATH

from lib.whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds, format_tc,
    parse_srt, write_srt, apply_fixes_to_srt, run_whisper,
    extract_audio_wav, classify_garbled_text,
    get_audio_duration, meaningful_char_count, is_length_anomaly,
    is_valid_subtitle_text,
)
setup_windows_utf8()


# ═══════════════════════════════════════════════════════════════
# FixReport
# ═══════════════════════════════════════════════════════════════

@dataclass
class FixReport:
    """Result of a fix run."""
    source: str = ''          # 'reference' | 'whisper' | 'checklist'
    applied: int = 0          # fixes written to SRT
    failed: int = 0           # couldn't fix → needs human
    deleted: int = 0          # cues removed (VAD non-speech)
    ai_review: int = 0        # needs AI confidence check
    tier: int = 0             # Whisper tier used (0 = N/A)
    details: list = field(default_factory=list)

    def merge(self, other: 'FixReport'):
        """Merge another report into this one."""
        self.applied += other.applied
        self.failed += other.failed
        self.deleted += other.deleted
        self.ai_review += other.ai_review
        self.details.extend(other.details)


# ═══════════════════════════════════════════════════════════════
# Baidu Translate helper — translates Whisper output (ja → target_lang)
# ═══════════════════════════════════════════════════════════════

def _translate_whisper_replacements(all_items: list, target_lang: str):
    """Translate Whisper replacement text from Japanese to target language.

    Only activates when target_lang ≠ 'ja'.  Uses Baidu Translate API if
    credentials are available; degrades gracefully otherwise (keeps original
    Japanese → AI fragments, where Claude translates directly).

    Each item's 'replacement' field is translated in-place.  The original
    Japanese text is preserved in 'whisper_original_ja' for AI context.
    """
    if target_lang == 'ja':
        return  # nothing to translate

    # Collect items with Whisper replacements
    to_translate = [f for f in all_items
                    if f.get('replacement', '').strip()]

    if not to_translate:
        return

    # Try to load Baidu credentials
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
              f'keeping Japanese originals (AI will translate {len(to_translate)} items)',
              file=sys.stderr)
        print(f'[translate] Set BAIDU_APPID + BAIDU_SECRET or ~/.baidu_translate '
              f'to enable auto-translation.',
              file=sys.stderr)
        return

    print(f'[translate] Baidu: translating {len(to_translate)} Whisper outputs '
          f'ja → {target_lang} ...', file=sys.stderr)

    translated_count = 0
    failed_count = 0
    for f in to_translate:
        jp_text = f['replacement'].strip()
        try:
            translated = baidu_translate(
                jp_text, appid, secret, source='ja', target=target_lang,
                endpoint=endpoint)
            if translated and translated != jp_text:
                f['whisper_original_ja'] = jp_text   # preserve for AI context
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
# Fixer
# ═══════════════════════════════════════════════════════════════

# Default Whisper paths — read from environment variables.
# Set WHISPER_CLI, WHISPER_MODEL, and WHISPER_RETRY_MODEL in your shell
# or pass whisper_cli=/model=/retry_model= to the Fixer constructor.
DEFAULT_WHISPER_CLI = os.environ.get('WHISPER_CLI', '')
DEFAULT_WHISPER_MODEL = os.environ.get('WHISPER_MODEL', '')
DEFAULT_RETRY_MODEL = os.environ.get('WHISPER_RETRY_MODEL', '')


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


class Fixer:
    """Fix garbled or mismatched subtitles for one episode.

    Three correction sources, cascading priority:
      Reference → Whisper → Human

    All corrections write to SRT and update the report through
    the same path. classify_garbled_text guarantees idempotency.
    """

    def __init__(self, episode: str, project_dir: str, *,
                 target_lang: str = 'ja',
                 video_dir: str = None,
                 whisper_cli: str = None,
                 model: str = None,
                 retry_model: str = None,
                 srt_dir: str = None):
        """Create a fix session for one episode.

        Args:
            episode: 'EP002'
            project_dir: project root (contains temp/, reports/)
            target_lang: target language 'ja' | 'zh'
            video_dir: video files directory (auto-detected if None)
            whisper_cli: path to whisper-cli.exe
            model: path to main Whisper model
            retry_model: path to fallback Whisper model
            srt_dir: subtitle files directory (default: from get_target_dir)
        """
        self.episode = episode
        self.project_dir = project_dir
        self.target_lang = target_lang

        # Paths
        from lib.project_utils import get_target_dir
        self._srt_dir = srt_dir or get_target_dir(project_dir)
        self._temp_dir = os.path.join(project_dir, 'temp', 'scans')
        self._report_path = os.path.join(project_dir, 'reports', '问题解决报告.md')
        self._review_dir = os.path.join(project_dir, 'reports', 'manual-review')

        # Video
        self._video_dir = video_dir
        self._video_path = None

        # Whisper
        self._whisper_cli = whisper_cli or DEFAULT_WHISPER_CLI
        self._model = model or DEFAULT_WHISPER_MODEL
        self._retry_model = retry_model or DEFAULT_RETRY_MODEL

        if not self._whisper_cli:
            print('[Fixer] WARNING: WHISPER_CLI env var not set. '
                  'Set it or pass whisper_cli= to use Whisper fixes.',
                  file=sys.stderr)

        # Lazy-loaded state
        self._srt_path = None
        self._srt_name = None
        self._cues = None
        self._episode_title = None  # extracted from video filename
        self._ref_srt_path = None   # reference subtitle for context injection (NO translation)
        self._ref_cues = None       # parsed reference cues, lazy-loaded

        self._resolve_paths()

    # ── Path resolution ──

    def _resolve_paths(self):
        """Find SRT/ASS and video files for this episode."""
        # Build search token: for EP###, use the number; for non-EP, use the stem
        if self.episode.startswith('EP') and len(self.episode) > 2 and self.episode[2:].isdigit():
            search_token = self.episode[2:]  # '064' from 'EP064'
        else:
            search_token = self.episode  # file stem, e.g. 'Sally the Witch (1990)'

        # Find SRT/ASS
        if os.path.isdir(self._srt_dir):
            for fname in sorted(os.listdir(self._srt_dir)):
                if not fname.endswith(('.srt', '.ass')):
                    continue
                stem = os.path.splitext(fname)[0]
                if search_token.isdigit():
                    if search_token in fname:
                        self._srt_path = os.path.join(self._srt_dir, fname)
                        self._srt_name = fname
                        break
                elif stem == search_token:
                    self._srt_path = os.path.join(self._srt_dir, fname)
                    self._srt_name = fname
                    break

        # Find video
        candidates = []
        if self._video_dir:
            candidates.append(self._video_dir)
        candidates.extend([
            os.path.join(self.project_dir, 'video'),
            os.path.join(self.project_dir, 'videos'),
        ])
        exts = ('.mkv', '.mp4', '.avi', '.mov')
        for vdir in candidates:
            if not os.path.isdir(vdir):
                continue
            for fname in sorted(os.listdir(vdir)):
                if search_token in fname and fname.lower().endswith(exts):
                    self._video_path = os.path.join(vdir, fname)
                    self._video_dir = vdir
                    # Extract episode title from filename
                    # e.g. "[Anonymoose] 鉄腕アトム - 031 - 黒い宇宙線 (DVD..."
                    m = re.search(r'-\s*\d{3}\s*-\s*(.+?)\s*\(', fname)
                    if m:
                        self._episode_title = m.group(1).strip()
                    return

    # ── Detection ──

    def is_clean(self) -> bool:
        """Check if SRT has any garbled cues remaining.

        Fast scan — no audio, no Whisper. Reads SRT, runs
        classify_garbled_text on every cue. Returns True if
        all cues are clean target-language text.
        """
        if not self._srt_path or not os.path.exists(self._srt_path):
            # No SRT → nothing to fix → clean
            return True
        cues = parse_srt(self._srt_path, mark_garbled=True,
                         target_lang=self.target_lang)
        return not any(c.get('is_garbled') for c in cues)

    def problem_count(self) -> int:
        """Count garbled cues in current SRT."""
        if not self._srt_path or not os.path.exists(self._srt_path):
            return 0
        cues = parse_srt(self._srt_path, mark_garbled=True,
                         target_lang=self.target_lang)
        return sum(1 for c in cues if c.get('is_garbled'))

    # ── Cue loading ──

    def _load_cues(self):
        """Load current SRT cues into memory."""
        if self._cues is None and self._srt_path:
            self._cues = parse_srt(self._srt_path, mark_garbled=True,
                                   target_lang=self.target_lang)
        return self._cues

    # ═══════════════════════════════════════════════════════════
    # Source 1: Reference comparison
    # ═══════════════════════════════════════════════════════════

    def fix_by_reference(self, reference_srt: str, *,
                         threshold: float = 0.4) -> FixReport:
        """Fix mismatches by comparing with a translated reference SRT.

        Parses the reference, compares each cue by time overlap with
        the current SRT, and adopts reference text for mismatched cues.

        Args:
            reference_srt: path to translated reference SRT
            threshold: similarity threshold (0.4 = 40%)

        Returns:
            FixReport with applied/failed counts
        """
        if not os.path.exists(reference_srt):
            return FixReport(source='reference', failed=0,
                             details=[f'Reference SRT not found: {reference_srt}'])

        if not self._srt_path:
            return FixReport(source='reference', failed=0,
                             details=['No current SRT to compare'])

        from fix.compare_srt import compare as compare_cues, parse_srt as parse_ref

        current_cues = parse_srt(self._srt_path, mark_garbled=False)
        ref_cues = parse_ref(reference_srt)
        diffs = compare_cues(current_cues, ref_cues, threshold=threshold)

        report = FixReport(source='reference')
        fixes = []
        unfixable = []
        fixed_starts = set()

        for d in diffs:
            if d['verdict'] == 'match':
                continue
            elif d['verdict'] == 'mismatch':
                # Adopt reference text
                for cue in current_cues:
                    if cue['start'] == d['start']:
                        fixes.append({
                            'start': cue['start'], 'end': cue['end'],
                            'original': cue['text'][:80],
                            'replacement': d['reference'][:200],
                            'confidence': 'high',
                            'model': 'reference',
                        })
                        fixed_starts.add(cue['start'])
                        break
            else:  # suspicious
                unfixable.append({
                    'ep': self.episode,
                    'time': d['start'],
                    'original': d['whisper'][:80],
                    'corrected': d['reference'][:200],
                    'status': '⬜',
                })

        # Apply fixes to SRT
        if fixes:
            apply_fixes_to_srt(self._srt_path, fixes)
            report.applied = len(fixes)

        # SRT is the source of truth — no report write needed for fixes.
        # Unfixable items → save to temp JSON for step_deliver() pick-up.
        if unfixable:
            pending_path = os.path.join(self._temp_dir,
                                        f'{self.episode}_pending_human.json')
            os.makedirs(self._temp_dir, exist_ok=True)
            with open(pending_path, 'w', encoding='utf-8') as fp:
                json.dump(unfixable, fp, ensure_ascii=False, indent=2)

        report.failed = len(unfixable)
        report.details = [f'{d["verdict"]}: {d.get("whisper","")[:60]}' for d in diffs
                          if d['verdict'] != 'match']

        print(f'[reference] {report.applied} fixed, {report.failed} suspicious',
              file=sys.stderr)
        return report

    # ═══════════════════════════════════════════════════════════
    # Source 2: Whisper
    # ═══════════════════════════════════════════════════════════

    def fix_by_whisper(self, *, separate_vocals: bool = True,
                       force_tier2: bool = False,
                       skip_vad_clean: bool = False,
                       detect_missing_dialogue: bool = True,
                       missing_dialogue_min_gap: float = 3.0) -> FixReport:
        """Run Whisper auto-fix pipeline.

        1. VAD clean: delete non-speech cues
        2. Missing dialogue detection: find VAD speech without subtitle
           coverage → insert ⚠SPEECH placeholder cues (routed to Whisper)
        3. Build clusters with context wrapping
        4. Tier 1 (segment-based) → auto-upgrade to Tier 2 if needed
        5. Apply fixes to SRT
        6. Triage: auto-keep / AI fragments / auto-cut
           - ⚠SPEECH placeholders: Whisper success → transcribed text;
             Whisper failure → [???] marker
        7. Safety scan: any residual ⚠SPEECH → [???]
        8. Update report: Layer 2 ✅ for fixed, Layer 6 ⬜ for unfixable

        Returns FixReport.
        """
        if not self._video_path:
            msg = (f'[whisper] {self.episode}: No video file found.\n'
                   f'  Searched: video_dir={self._video_dir or "(not set)"}, '
                   f'project/video, project/videos.\n'
                   f'  Fix: pass --video-dir, or add the path to CLAUDE.md '
                   f'under 「项目路径」with 「视频」in the description.')
            print(msg, file=sys.stderr)
            return FixReport(source='whisper', failed=0,
                             details=['No video file found'])

        if not self._srt_path:
            msg = (f'[whisper] {self.episode}: No SRT file found in '
                   f'{self._srt_dir}')
            print(msg, file=sys.stderr)
            return FixReport(source='whisper', failed=0,
                             details=['No SRT file to fix'])

        cues = parse_srt(self._srt_path, mark_garbled=True,
                         target_lang=self.target_lang)
        original_count = len(cues)
        garbled = [c for c in cues if c.get('is_garbled')]

        if not garbled:
            print(f'[whisper] {self.episode}: all clean, nothing to fix',
                  file=sys.stderr)
            return FixReport(source='whisper')

        print(f'[whisper] {self.episode}: {len(garbled)}/{original_count} garbled cues',
              file=sys.stderr)

        tmpdir = tempfile.mkdtemp()
        try:
            deleted_count = 0

            # Step 1: VAD clean — try cached speech timeline from Phase 1 first
            speech_segs = []
            if not skip_vad_clean:
                from fix.whisper_pipeline import vad_delete_nonspeech

                # Try loading cached VAD from Phase 1 scan (avoids re-extracting audio)
                cached_segs = self._load_speech_segs()

                if cached_segs:
                    # Phase 1 already extracted audio + ran VAD — reuse speech timeline
                    print(f'[whisper] Using cached VAD from Phase 1 '
                          f'({len(cached_segs)} speech segments)',
                          file=sys.stderr)
                    # We still need to run vad_delete_nonspeech to clean cues,
                    # but we can skip the audio extraction. However,
                    # vad_delete_nonspeech takes an audio_path and extracts
                    # VAD internally. Since we already have speech_segs,
                    # apply the deletion logic directly.
                    cues, deleted, speech_segs = _apply_vad_clean_from_cache(
                        cached_segs, cues, self._srt_path,
                        target_lang=self.target_lang)
                    deleted_count = len(deleted)
                else:
                    # Fallback: extract audio + run VAD (original path)
                    vad_audio = os.path.join(tmpdir, 'vad_full.wav')
                    try:
                        extract_audio_wav(self._video_path, vad_audio)
                        cues, deleted, speech_segs = vad_delete_nonspeech(
                            vad_audio, cues, self._srt_path,
                            target_lang=self.target_lang)
                        deleted_count = len(deleted)
                        # Persist speech_segs for later use (human review VAD alignment)
                        self._save_speech_segs(speech_segs)
                    except Exception as e:
                        print(f'[whisper] VAD audio extraction failed: {e}',
                              file=sys.stderr)
                        print(f'[whisper] Continuing without VAD clean',
                              file=sys.stderr)

            # ── Missing dialogue detection: FALLBACK only ──
            # Phase 1 now handles VAD gap detection. This inline detection
            # only runs as fallback when Phase 1 didn't produce findings.
            placeholder_count = 0
            if detect_missing_dialogue and speech_segs:
                # Check if Phase 1 already found gaps for this episode
                already_detected = self._has_missing_subtitles()
                if not already_detected:
                    from fix.whisper_pipeline import (
                        find_missing_subtitle_gaps, add_placeholder_cues,
                    )
                    gaps = find_missing_subtitle_gaps(
                        speech_segs, cues, min_gap=missing_dialogue_min_gap,
                        max_gap=45.0)
                    if gaps:
                        cues = add_placeholder_cues(gaps, cues, self._srt_path)
                        placeholder_count = len(gaps)
                        print(f'[whisper] {placeholder_count} missing-dialogue '
                              f'gaps → ⚠SPEECH placeholders added (fallback)',
                              file=sys.stderr)
                else:
                    print(f'[whisper] Missing subtitles already detected by '
                          f'Phase 1 — skipping inline detection',
                          file=sys.stderr)

            # Reload garbled after VAD (includes ⚠SPEECH placeholders)
            garbled = [c for c in cues if c.get('is_garbled')]
            if not garbled:
                # If we added placeholders, they should be garbled —
                # double-check and force-reparse if needed
                if placeholder_count > 0:
                    # Re-parse with fresh garbled detection
                    cues = parse_srt(self._srt_path, mark_garbled=True,
                                     target_lang=self.target_lang)
                    garbled = [c for c in cues if c.get('is_garbled')]
                    if not garbled:
                        print(f'[whisper] ⚠ Placeholders added but not '
                              f'detected as garbled — skipping Whisper',
                              file=sys.stderr)
                        # Convert placeholders directly to [???]
                        self._convert_placeholders_to_markers()
                        return FixReport(source='whisper',
                                        deleted=deleted_count,
                                        details=[f'{placeholder_count} '
                                                 f'missing-dialogue markers'])
                else:
                    print(f'[whisper] All garbled cues deleted by VAD',
                          file=sys.stderr)
                    return FixReport(source='whisper', deleted=deleted_count)

            # Step 2: Build clusters + run Whisper
            from fix.whisper_pipeline import (
                build_clusters, run_tier1, run_tier2,
                UPGRADE_THRESHOLD,
            )

            if force_tier2 or len(garbled) > UPGRADE_THRESHOLD:
                tier = 2
                print(f'[whisper] {len(garbled)} fragments > '
                      f'{UPGRADE_THRESHOLD} → Tier 2 (full-episode)',
                      file=sys.stderr)
                fixes, stats = run_tier2(
                    self._video_path, cues,
                    self._whisper_cli, self._model,
                    self.target_lang, tmpdir,
                    separate_vocals_flag=separate_vocals,
                    save_transcript_to=os.path.join(
                        self._temp_dir, f'whisper_full_{self.episode}.json'),
                )
            else:
                tier = 1
                clusters = build_clusters(cues)
                if not clusters:
                    fixes, _ = [], []
                else:
                    print(f'[whisper] Tier 1: {len(clusters)} clusters',
                          file=sys.stderr)
                    fixes, unmatched_cues = run_tier1(
                        self._video_path, cues, clusters,
                        self._whisper_cli, self._model,
                        self.target_lang, tmpdir,
                        separate_vocals_flag=separate_vocals,
                    )

                    # Retry with backup model
                    if unmatched_cues and self._retry_model:
                        print(f'[whisper] {len(unmatched_cues)} unmatched → '
                              f'retry with backup model', file=sys.stderr)
                        retry_clusters = build_clusters(cues)
                        if retry_clusters:
                            retry_fixes, _ = run_tier1(
                                self._video_path, cues, retry_clusters,
                                self._whisper_cli, self._retry_model,
                                self.target_lang, tmpdir,
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
                            self.target_lang, tmpdir,
                            separate_vocals_flag=separate_vocals,
                            save_transcript_to=os.path.join(
                                self._temp_dir, f'whisper_full_{self.episode}.json'),
                        )
                        t1_success = {f['start'] for f in fixes
                                      if f['confidence'] != 'none'}
                        merged = [f for f in fixes
                                  if f['confidence'] != 'none']
                        merged.extend([f for f in t2_fixes
                                       if f['start'] not in t1_success])
                        fixes = merged
                        tier = 2

            # Step 3: Triage FIRST — classify before writing to SRT
            #
            #  eval_text = Whisper replacement, or original if Whisper failed
            #
            #  ① meaningful_jp < 2  → auto-cut (pure Latin, bare exclamations)
            #  ② looks plausible     → auto-keep (write to SRT ✅)
            #  ③ rest                → L2.5 AI completion (temp JSON, NOT to SRT)
            #       └─ AI fills → apply → VAD check → cut or escalate to L6 human
            report = FixReport(source='whisper', tier=tier,
                              deleted=deleted_count)

            from lib.whisper_utils import looks_like_plausible_text

            auto_keep = []
            ai_fragments = []        # → L2.5 AI上下文补全
            auto_cut = []            # → 直接删除 (meaningful chars < 2)

            all_items = list(fixes)

            # Fallback: if Whisper produced no fixes, evaluate garbled cues directly
            if not fixes and garbled:
                print(f'[whisper] No Whisper output — evaluating {len(garbled)} '
                      f'garbled cue(s) directly', file=sys.stderr)
                for g in garbled:
                    all_items.append({
                        'start': g['start'], 'end': g['end'],
                        'original': g['text'], 'replacement': None,
                        'confidence': 'none', 'model': 'tier1',
                    })

            # ── Baidu Translate: Whisper output (Japanese) → target language ──
            # Only activated when target_lang ≠ 'ja' (e.g., --lang zh).
            # Graceful degradation: if Baidu credentials are missing or API fails,
            # keep original Japanese text → AI fragments (AI translates directly).
            _translate_whisper_replacements(all_items, self.target_lang)

            for f in all_items:
                # eval_text: prefer Whisper output, fall back to original
                eval_text = (f.get('replacement') or f.get('original', '')).strip()

                # ── Placeholder handling: ⚠SPEECH markers for missing dialogue ──
                # These are NOT real garbled cues — they're sentinels we inserted
                # where VAD detected speech but no subtitle existed.
                # Detect by original text (more robust than flag propagation).
                if f.get('original', '').strip() == '⚠SPEECH':
                    replacement = f.get('replacement', '').strip()
                    if replacement and replacement != '⚠SPEECH':
                        # Whisper transcribed the audio.
                        # For non-JP projects: translated text (via Baidu) → auto-keep;
                        # untranslated Japanese → AI fragments for Claude to translate.
                        if self.target_lang != 'ja':
                            if looks_like_plausible_text(replacement, self.target_lang):
                                auto_keep.append(f)
                            else:
                                # Japanese output needs translation → AI review
                                ai_fragments.append(f)
                        else:
                            auto_keep.append(f)
                    else:
                        # Whisper couldn't transcribe → [???] marker
                        f['replacement'] = '[???]'
                        auto_keep.append(f)
                    continue

                # ① Pre-filter: no meaningful characters in target language → auto-cut
                if meaningful_char_count(eval_text, self.target_lang) < 2:
                    auto_cut.append(f)
                    continue

                # ② Readable target language → auto-keep (unless hallucination-suspect)
                if looks_like_plausible_text(eval_text, self.target_lang):
                    original = f.get('original', '')
                    # Only check length anomaly if the ORIGINAL had meaningful
                    # content.  Pure noise has nothing to preserve — Whisper
                    # output is always an improvement.
                    if (f.get('replacement')
                            and meaningful_char_count(original, self.target_lang) >= 2
                            and is_length_anomaly(original, eval_text)):
                        ai_fragments.append(f)
                    else:
                        auto_keep.append(f)
                    continue

                # ③ Has target-language content but with corruption → AI completion
                ai_fragments.append(f)

            # ── Apply auto-cuts: delete noise cues from SRT ──
            if auto_cut:
                cut_starts = {f['start'] for f in auto_cut}
                kept_cues = [c for c in cues if c.get('start') not in cut_starts]
                deleted_now = len(cues) - len(kept_cues)
                if deleted_now > 0:
                    write_srt(self._srt_path, kept_cues)
                    cues[:] = kept_cues
                    report.deleted += deleted_now
                    print(f'[whisper] Auto-cut {deleted_now} noise cues '
                          f'(meaningful_jp < 2)', file=sys.stderr)
                    for f in auto_cut:
                        print(f'  [cut] {f["start"]}: '
                              f'"{f.get("original", "")[:60]}"', file=sys.stderr)

            # ── Apply ONLY auto-keep fixes to SRT ──
            # AI fragments are NOT written to SRT — they stay as original
            # garbled text until AI provides a correction.
            if auto_keep:
                keep_fixes = [f for f in auto_keep if f.get('replacement')]
                if keep_fixes:
                    apply_fixes_to_srt(self._srt_path, keep_fixes)

            # ── AI-fixable fragments → write temp JSON for AI review ──
            if ai_fragments:
                self._write_ai_fragments_json(ai_fragments)

            # ── Final safety scan: convert any remaining ⚠SPEECH → [???] ──
            # Handles edge cases where placeholders bypassed the triage
            # (e.g., Whisper produced no output, or placeholder was in
            #  a cluster that failed to extract properly).
            marker_count = self._convert_placeholders_to_markers()

            report.applied = len(auto_keep)
            report.ai_review = len(ai_fragments)
            report.failed = 0   # nothing goes directly to L6 anymore
            report.details = (['⚠SPEECH → [???]'] * marker_count
                            if marker_count else [])

            # ── Write to 问题解决报告 ──
            try:
                if auto_keep:
                    upsert_entries(self._report_path, step='2', entries=[
                        {'ep': self.episode, 'time': f.get('start', ''),
                         'original': f.get('original', '')[:120],
                         'corrected': (f.get('replacement') or '')[:120],
                         'status': '✅'}
                        for f in auto_keep
                    ])
                if auto_cut:
                    upsert_entries(self._report_path, step='2', entries=[
                        {'ep': self.episode, 'time': f.get('start', ''),
                         'original': f.get('original', '')[:120],
                         'corrected': '(VAD已删除)', 'status': '🗑️'}
                        for f in auto_cut
                    ])
                if ai_fragments:
                    # NOTE: corrected 预填 Whisper 猜测（非 AI 审查结果）。
                    # ⬜ 状态 + 非空 corrected = "Whisper有猜测，待AI确认"。
                    # AI审查（--apply-ai-review）后会通过 update_entry_status
                    # 完全覆盖此字段，预填值仅供审查时参考，不影响最终结果。
                    upsert_entries(self._report_path, step='2.5', entries=[
                        {'ep': self.episode, 'time': f.get('start', ''),
                         'original': f.get('original', '')[:120],
                         'corrected': (f.get('replacement') or '')[:120],
                         'status': '⬜'}
                        for f in ai_fragments
                    ])
            except Exception as e:
                import traceback
                print(f'[whisper] Report write failed: {e}', file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

            print(f'[whisper] {report.applied} auto-keep, '
                  f'{report.ai_review} → AI, '
                  f'{len(auto_cut)} auto-cut, '
                  f'{report.deleted} total deleted', file=sys.stderr)

            return report

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════
    # Source 2b: Missing subtitle fill (VAD gaps → Whisper → new cues)
    # ═══════════════════════════════════════════════════════════

    def fix_missing_subtitles(self, gaps=None) -> FixReport:
        """Fill missing subtitles detected by VAD scan (Layer 1).

        Full triage pipeline (same as garbled-cue path):
          1. Extract gap audio → Whisper transcription
          2. Triage: auto-keep ✅ / AI fragments 🤖 / auto-cut 🗑️ / [???] ⬜
          3. Auto-keep → inserted into SRT; AI fragments → [???] + JSON for review
          4. Write ai_fragments_{EP}.json for AI review
          5. Update 问题解决报告.md

        Args:
            gaps: optional explicit gap list [{start_s, end_s, duration}, ...].
                  If None, reads from findings.json missing_subtitles[episode].

        Returns:
            FixReport with applied/ai_review/failed counts
        """
        # ── Resolve gaps ──
        if gaps is None:
            findings_path = os.path.join(self._temp_dir, 'findings.json')
            if os.path.exists(findings_path):
                try:
                    with open(findings_path, 'r', encoding='utf-8') as f:
                        findings = json.load(f)
                    gaps = findings.get('missing_subtitles', {}).get(self.episode, [])
                except Exception:
                    gaps = []
            else:
                gaps = []

        if not gaps:
            return FixReport(source='missing_sub', details=['No gaps to fill'])

        if not self._video_path:
            return FixReport(source='missing_sub', failed=len(gaps),
                            details=['No video file available'])

        if not self._whisper_cli or not self._model:
            return FixReport(source='missing_sub', failed=len(gaps),
                            details=['Whisper not configured'])

        print(f'[missing_sub] {self.episode}: {len(gaps)} gaps → Whisper fill + triage',
              file=sys.stderr)

        cues = (self._load_cues() or
                parse_srt(self._srt_path, mark_garbled=False,
                         target_lang=self.target_lang))

        tmpdir = tempfile.mkdtemp()
        try:
            from lib.whisper_utils import format_tc, looks_like_plausible_text

            ORIGINAL_MARKER = '(VAD检测到人声但无字幕)'

            # Phase 1: Collect Whisper results as fix items
            fix_items = []  # {start, end, start_s, end_s, original, replacement}

            for i, gap in enumerate(gaps):
                ss = gap.get('start_s', 0)
                es = gap.get('end_s', ss + 5.0)
                dur = es - ss

                # Skip implausibly short or long gaps
                if dur < 1.0:
                    print(f'  [gap {i}] {ss:.1f}s–{es:.1f}s ({dur:.1f}s) — '
                          f'too short, skipping', file=sys.stderr)
                    continue
                if dur > 60.0:
                    print(f'  [gap {i}] {ss:.1f}s–{es:.1f}s ({dur:.1f}s) — '
                          f'too long, skipping', file=sys.stderr)
                    continue

                start_tc = format_tc(ss)
                end_tc = format_tc(es)
                item = {
                    'start': start_tc,
                    'end': end_tc,
                    'start_s': ss,
                    'end_s': es,
                    'original': ORIGINAL_MARKER,
                    'replacement': None,
                }

                # Extract gap audio
                gap_wav = os.path.join(tmpdir, f'gap_{i:03d}.wav')
                try:
                    extract_audio_wav(self._video_path, gap_wav,
                                     ss=ss, duration=dur)
                except Exception as e:
                    print(f'  [gap {i}] Audio extraction failed: {e}',
                          file=sys.stderr)
                    item['replacement'] = None  # → [???]
                    fix_items.append(item)
                    continue

                # Run Whisper on gap audio
                whisper_segs = run_whisper(gap_wav, self._whisper_cli,
                                          self._model, self.target_lang)

                if whisper_segs:
                    text = whisper_segs[0].get('text', '').strip()
                    if text and is_valid_subtitle_text(text, self.target_lang):
                        item['replacement'] = text
                        print(f'  [gap {i}] {start_tc}–{end_tc} '
                              f'→ "{text[:60]}"', file=sys.stderr)
                    else:
                        item['replacement'] = None
                        print(f'  [gap {i}] {start_tc}–{end_tc} '
                              f'→ [???] (Whisper: no valid text)',
                              file=sys.stderr)
                else:
                    item['replacement'] = None
                    print(f'  [gap {i}] {start_tc}–{end_tc} '
                          f'→ [???] (Whisper: no output)',
                          file=sys.stderr)

                fix_items.append(item)

            # Phase 2: Triage — mirror garbled-cue path exactly
            #   auto-keep → write Whisper text to SRT, report step 2 ✅
            #   rest      → AI fragment (Whisper text stays in SRT as placeholder,
            #                ai_fragments JSON for AI review, report step 2.5 ⬜)
            #   [???] never appears here — it only emerges after AI review fails
            #   (apply_ai_fragments escalates unfilled items → pending_human.json
            #    → step_deliver generates human checklist with [???] markers)
            auto_keep = []
            ai_fragments = []

            for f in fix_items:
                replacement = f.get('replacement', '')

                if not replacement:
                    # Whisper produced nothing — keep original marker as placeholder
                    # in SRT; AI review will see empty whisper_attempt and escalate
                    f['replacement'] = ORIGINAL_MARKER
                    ai_fragments.append(f)
                    continue

                eval_text = replacement.strip()

                # Plausible, meaningful text → auto-keep (direct to SRT)
                if (meaningful_char_count(eval_text, self.target_lang) >= 2
                        and looks_like_plausible_text(eval_text, self.target_lang)):
                    auto_keep.append(f)
                    continue

                # Everything else → AI fragment review
                # Whisper text stays in SRT as placeholder (better than silence)
                ai_fragments.append(f)

            # Phase 3: Build new cues for SRT insertion
            new_cues = []
            for f in auto_keep:
                new_cues.append({
                    'start': f['start'],
                    'end': f['end'],
                    'start_s': f['start_s'],
                    'end_s': f['end_s'],
                    'text': f.get('replacement', ORIGINAL_MARKER),
                })

            for f in ai_fragments:
                # Keep Whisper text in SRT as temp placeholder — AI review will fix
                text = f.get('replacement', ORIGINAL_MARKER)
                new_cues.append({
                    'start': f['start'],
                    'end': f['end'],
                    'start_s': f['start_s'],
                    'end_s': f['end_s'],
                    'text': text,
                })

            # Phase 4: Write to SRT
            if new_cues:
                all_cues = cues + new_cues
                all_cues.sort(key=lambda c: c['start_s'])
                write_srt(self._srt_path, all_cues)
                self._cues = all_cues  # update cache

            # Phase 5: Write AI fragments JSON for review
            if ai_fragments:
                self._write_ai_fragments_json(ai_fragments)

            # Phase 6: Update report
            report = FixReport(source='missing_sub')
            report.applied = len(auto_keep)
            report.ai_review = len(ai_fragments)
            report.failed = 0

            try:
                if auto_keep:
                    upsert_entries(self._report_path, step='2', entries=[
                        {'ep': self.episode,
                         'time': f['start'],
                         'original': ORIGINAL_MARKER,
                         'corrected': f.get('replacement', '')[:120],
                         'status': '✅'}
                        for f in auto_keep
                    ])
                if ai_fragments:
                    # NOTE: corrected 预填 Whisper 猜测（非 AI 审查结果）。
                    # ⬜ 状态 + 非空 corrected = "Whisper有猜测，待AI确认"。
                    # AI审查（--apply-ai-review）后会通过 apply_ai_fragments
                    # 完全覆盖此字段。AI 无法填充的 → escalate → pending_human.json
                    # → step_deliver 生成 human checklist（[???] 只出现在那里）。
                    upsert_entries(self._report_path, step='2.5', entries=[
                        {'ep': self.episode,
                         'time': f['start'],
                         'original': ORIGINAL_MARKER,
                         'corrected': (f.get('replacement') or '')[:120],
                         'status': '⬜'}
                        for f in ai_fragments
                    ])
            except Exception as e:
                print(f'[missing_sub] Report write failed: {e}',
                      file=sys.stderr)

            print(f'[missing_sub] {self.episode}: {report.applied} auto-keep ✅, '
                  f'{report.ai_review} → AI review 🤖',
                  file=sys.stderr)

            return report

        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ═══════════════════════════════════════════════════════════
    # Cascading auto-fix
    # ═══════════════════════════════════════════════════════════

    def run_auto(self) -> FixReport:
        """Run cascading auto-fix: whisper → missing-sub fill → AI context.

        Priority:
        1. If reference SRT exists in 参考字幕/ → inject as AI context
           (original language, NO machine translation — Claude reads directly)
        2. Whisper ASR → triage (auto-keep / AI fragments / auto-cut)
        3. Missing subtitle fill (VAD gaps → Whisper → new cues)
        4. AI fragments include reference_text for Claude to translate+correct
        5. For still unfixable → review (generate checklist)

        Returns combined FixReport.
        """
        # Check for missing subtitles (Layer 1 VAD-detected gaps)
        has_missing_subs = self._has_missing_subtitles()

        if self.is_clean() and not has_missing_subs:
            print(f'[{self.episode}] ✓ clean — nothing to fix', file=sys.stderr)
            return FixReport(source='auto')

        report = FixReport(source='auto')

        # Priority 1: Find reference SRT for AI context injection
        # Reference text is kept in its ORIGINAL language — no Baidu Translate.
        # Claude reads the reference directly in ai_fragments_{EP}.json and
        # produces the target-language correction with full context.
        ref_dir = os.path.join(self.project_dir, '参考字幕')
        if os.path.isdir(ref_dir):
            ep_num = self.episode[2:]
            for fname in os.listdir(ref_dir):
                if fname.endswith('.srt') and ep_num in fname:
                    self._ref_srt_path = os.path.join(ref_dir, fname)
                    print(f'[{self.episode}] Reference SRT found → '
                          f'will inject as AI context (no translation)',
                          file=sys.stderr)
                    break

        # Priority 2: Whisper for garbled cues
        if not self.is_clean() and self._video_path:
            whisper_result = self.fix_by_whisper()
            report.merge(whisper_result)

        # Priority 3: Missing subtitle fill (v5.0 — Layer 1 VAD gaps)
        if has_missing_subs and self._video_path:
            miss_result = self.fix_missing_subtitles()
            report.merge(miss_result)

        # Human review checklist is generated later by run_all.py:step_deliver().
        # Don't generate it here — that would create a duplicate flat-file
        # checklist alongside the per-ep-folder one.

        print(f'\n[{self.episode}] Auto-fix complete: '
              f'{report.applied} applied, {report.failed} → manual review',
              file=sys.stderr)
        return report

    def _has_missing_subtitles(self) -> bool:
        """Check if findings.json has missing_subtitles for this episode."""
        findings_path = os.path.join(self._temp_dir, 'findings.json')
        if not os.path.exists(findings_path):
            return False
        try:
            with open(findings_path, 'r', encoding='utf-8') as f:
                findings = json.load(f)
            gaps = findings.get('missing_subtitles', {}).get(self.episode, [])
            return len(gaps) > 0
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════
    # Source 3: Human review
    # ═══════════════════════════════════════════════════════════

    def review_from_items(self, items: list,
                            output_dir: str = None) -> str | None:
        """Generate manual review checklist + video clips from explicit item list.

        Like review() but accepts pending items directly instead of reading
        from the report. Each item dict has keys: 'ep', 'time', 'original'.

        Returns:
            Path to checklist.md, or None if no items
        """
        if not items:
            print(f'[{self.episode}] review_from_items: empty list',
                  file=sys.stderr)
            return None

        out_dir = output_dir or self._review_dir
        is_per_ep = bool(output_dir)

        pending = [e for e in items if e.get('ep') == self.episode]
        if not pending:
            print(f'[{self.episode}] review_from_items: nothing for this ep',
                  file=sys.stderr)
            return None

        # Load cues and build clusters (same logic as Whisper)
        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=True, target_lang=self.target_lang)
        clusters = self._build_review_clusters(cues, pending)

        if not clusters:
            print(f'[{self.episode}] review_from_items: could not build clusters',
                  file=sys.stderr)
            return None

        os.makedirs(out_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries_md = []
        extracted = 0
        skipped = 0

        for cl in clusters:
            first_g = cl['garbled'][0]
            last_g = cl['garbled'][-1]
            start_ts = first_g['start']
            safe_start = start_ts.replace(':', '-').replace(',', '-').replace('.', '-')

            clip_name = f'{safe_start}.mp4'
            clip_path = os.path.join(out_dir, clip_name)

            garbled_start = first_g['start_s']
            garbled_end = last_g['end_s']

            bounds = self._compute_review_clip_bounds(
                garbled_start, garbled_end, cl.get('ss'), cl.get('es'))

            left_text = cl.get('left_text', '')
            right_text = cl.get('right_text', '')
            garbled_texts = ' | '.join(g['text'][:60] for g in cl['garbled'])

            if bounds is not None:
                clip_start, clip_end = bounds
                if not os.path.exists(clip_path):
                    if self._video_path:
                        ok = self._extract_clip(self._video_path, clip_start,
                                                clip_end, clip_path)
                        if ok:
                            extracted += 1
                        else:
                            skipped += 1
                    else:
                        clip_name = '(无片段 — 未提供视频路径)'
                        skipped += 1
                        print(f'  ⚠ [{self.episode}] No video path — '
                              f'pass --video-dir to extract clips.',
                              file=sys.stderr)
                elif os.path.exists(clip_path):
                    extracted += 1
            else:
                clip_name = '(无片段 — VAD未检测到语音)'
                skipped += 1

            entries_md.append(
                f'{self.episode} | {start_ts} ~ {first_g.get("end", "?")}\n'
                f'来源: Whisper unfixable\n'
                f'上文: {left_text[:120] if left_text else "(无)"}\n'
                f'残留: {garbled_texts}\n'
                f'下文: {right_text[:120] if right_text else "(无)"}\n'
                f'片段: {clip_name}\n'
                f'修正:\n'
                f'\n---\n'
            )

        if is_per_ep:
            checklist_path = os.path.join(out_dir, 'checklist.md')
        else:
            checklist_path = os.path.join(out_dir, f'{self.episode}_checklist.md')
        with open(checklist_path, 'w', encoding='utf-8') as f:
            f.write(f'# 人工审查清单 — {self.episode}\n'
                    f'> 导出: {today}\n'
                    f'> 共 {len(entries_md)} 条待审查\n'
                    f'> version: 2\n'
                    f'>\n'
                    f'> **填写方法**：看视频 + 读上下文 → 写出正确台词。\n'
                    f'> 写「删除」移除该 cue。填完运行 --apply-checklist。\n'
                    f'>\n'
                    f'---\n\n')
            for entry_md in entries_md:
                f.write(entry_md)

        self._save_review_clusters(clusters)

        print(f'[{self.episode}] review_from_items: {len(entries_md)} entries → '
              f'{checklist_path} ({extracted} clips, {skipped} skipped)',
              file=sys.stderr)
        return checklist_path

    def review(self, output_dir: str = None) -> str | None:
        """[Legacy] Generate manual review checklist by reading report L6.

        Delegates to review_from_items() after loading from report.
        """
        from utils.update_report import read_report
        data = read_report(self._report_path)
        entries = data.get('6', [])
        pending = [e for e in entries
                   if e.get('status') == '⬜' and e.get('ep') == self.episode]

        if not pending:
            print(f'[{self.episode}] review: nothing pending', file=sys.stderr)
            return None

        return self.review_from_items(pending, output_dir)
    def _build_paired_cues(self, cues, target_fragment):
        """Build paired-cue list for hallucination-suspect fragments.

        Finds the best neighbor cue to pair with the target, allowing AI
        to edit either (or both) to make the combined passage coherent.
        """
        target_start = target_fragment.get('start', '')
        target_start_s = to_seconds(target_start) if target_start else 0
        target_end = target_fragment.get('end', '')
        target_end_s = to_seconds(target_end) if target_end else target_start_s + 3.0
        target_text = target_fragment.get('replacement', target_fragment.get('original', ''))

        candidates_before = []
        candidates_after = []
        for cue in cues:
            cs = cue.get('start_s', 0)
            ct = cue.get('text', '').strip()
            if not ct:
                continue
            if abs(cs - target_start_s) < 0.1:
                continue
            if abs(cs - target_start_s) > 5.0:
                continue
            is_garbled = cue.get('is_garbled', False)
            entry = {
                'start': cue.get('start', ''),
                'end': cue.get('end', ''),
                'text': ct,
                'is_garbled': is_garbled,
                'correction': '',
            }
            if cs < target_start_s:
                candidates_before.append(entry)
            else:
                candidates_after.append(entry)

        def _neighbor_score(c):
            return (0 if not c.get('is_garbled') else 1, len(c['text']))

        best_neighbor = None
        all_candidates = candidates_before + candidates_after
        if all_candidates:
            all_candidates.sort(key=_neighbor_score)
            best_neighbor = all_candidates[0]
            best_neighbor['role'] = 'neighbor'

        paired = []
        if best_neighbor:
            paired.append(best_neighbor)
        paired.append({
            'start': target_start,
            'end': target_end,
            'text': target_text,
            'role': 'target',
            'is_garbled': True,
            'correction': '',
        })
        return paired



    # ═══════════════════════════════════════════════════════════
    # Source 3b: AI short fragment completion (Layer 2.5)
    # ═══════════════════════════════════════════════════════════

    def _extract_whisper_context(self, start_s: float, end_s: float,
                                  window_s: float = 30.0) -> list:
        """Extract Whisper transcript segments within ±window_s of fragment.

        Reads temp/scans/whisper_full_{EP}.json (generated by Tier 2) and
        returns segments whose start time falls within the window.  These
        provide rich acoustic context for AI even when the specific cue's
        Whisper alignment failed (whisper_attempt=null).

        Returns:
            [{start_s: float, text: str}, ...] — compact, sorted by time
        """
        transcript_path = os.path.join(
            self._temp_dir, f'whisper_full_{self.episode}.json')
        if not os.path.exists(transcript_path):
            return []

        try:
            with open(transcript_path, 'r', encoding='utf-8') as fh:
                all_segs = json.load(fh)
        except Exception:
            return []

        window_segs = []
        for seg in all_segs:
            seg_s = seg.get('start_s', 0)
            if start_s - window_s <= seg_s <= end_s + window_s:
                window_segs.append({
                    'start_s': seg_s,
                    'text': seg.get('text', ''),
                })

        # Truncate: keep up to 20 segments, trimming from furthest first
        if len(window_segs) > 20:
            mid = (start_s + end_s) / 2
            window_segs.sort(key=lambda s: abs(s['start_s'] - mid))
            window_segs = window_segs[:20]
            window_segs.sort(key=lambda s: s['start_s'])

        return window_segs

    def _load_ref_cues(self):
        """Load parsed reference SRT cues (lazy, cached)."""
        if self._ref_cues is not None:
            return self._ref_cues
        if not self._ref_srt_path or not os.path.exists(self._ref_srt_path):
            self._ref_cues = []
            return self._ref_cues
        try:
            self._ref_cues = parse_srt(self._ref_srt_path, mark_garbled=False)
        except Exception:
            self._ref_cues = []
        return self._ref_cues

    def _find_ref_text(self, start_s: float, end_s: float,
                       max_chars: int = 200) -> str:
        """Find reference subtitle text overlapping a time range.

        Returns the text of the reference cue with the greatest time overlap,
        or empty string if none found.  Reference text is kept in its
        ORIGINAL language (no translation) — AI reads it as context and
        produces the target-language correction directly.
        """
        ref_cues = self._load_ref_cues()
        if not ref_cues:
            return ''

        best_overlap = 0.0
        best_text = ''
        for cue in ref_cues:
            overlap = min(end_s, cue['end_s']) - max(start_s, cue['start_s'])
            if overlap > best_overlap and overlap > 0:
                best_overlap = overlap
                best_text = cue.get('text', '').strip()

        if best_text and len(best_text) > max_chars:
            best_text = best_text[:max_chars] + '…'
        return best_text

    def _write_ai_fragments_json(self, fragments: list) -> str | None:
        """Write AI fragments to temp JSON for AI review (replaces ai_review.md).

        Each fragment dict has keys: 'start', 'end', 'original', 'replacement'.
        Writes to temp/scans/ai_fragments_{EP}.json — a machine-readable
        format that AI can fill and apply directly, no markdown parsing needed.

        Returns:
            Path to JSON file, or None if fragments is empty
        """
        if not fragments:
            return None

        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=False)

        os.makedirs(self._temp_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries = []

        for f in fragments:
            start_ts = f.get('start', '')
            end_ts = f.get('end', '')
            original = f.get('original', '')
            corrected = f.get('replacement', '')

            # ── Build wider SRT context (Level 1: 6 cues each side) ──
            start_s = to_seconds(start_ts) if start_ts else 0
            end_s = to_seconds(end_ts) if end_ts else start_s + 5.0

            context_before = []
            context_after = []
            for cue in sorted(cues, key=lambda c: c['start_s']):
                cs = cue.get('start_s', 0)
                ct = cue.get('text', '').strip()
                if ct:
                    if cs < start_s - 1:
                        context_before.append(ct)
                        if len(context_before) > 6:
                            context_before.pop(0)
                    elif cs > end_s + 1 and len(context_after) < 6:
                        context_after.append(ct)

            # ── Extract Whisper transcript window (Level 2: ±30s) ──
            whisper_ctx = self._extract_whisper_context(start_s, end_s)

            # ── Reference subtitle context (Level 3: original language) ──
            # If 参考字幕/ has a matching SRT, include the overlapping cue text
            # in its ORIGINAL language.  AI reads this directly — no machine
            # translation needed.  Claude translates + corrects in one step.
            ref_text = self._find_ref_text(start_s, end_s)

            entry = {
                'start': start_ts,
                'end': end_ts,
                'original': original[:200],
                'whisper_attempt': corrected,
                'whisper_original_ja': f.get('whisper_original_ja', ''),
                'context_before': context_before,
                'context_after': context_after,
                'whisper_context': whisper_ctx,
                'reference_text': ref_text,
                'correction': '',  # AI fills this
            }
            # Paired mode: hallucination-suspect fragments get neighbor cue edit ability
            if corrected and is_length_anomaly(original, corrected):
                entry['mode'] = 'paired'
                entry['paired_cues'] = self._build_paired_cues(cues, f)
            entries.append(entry)

        json_path = os.path.join(self._temp_dir, f'ai_fragments_{self.episode}.json')
        data = {
            'episode': self.episode,
            'episode_title': self._episode_title,
            'exported': today,
            'fragments': entries,
        }
        with open(json_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

        print(f'[{self.episode}] _write_ai_fragments_json: {len(entries)} '
              f'entries → {json_path}', file=sys.stderr)
        return json_path

    def apply_ai_fragments(self, json_path: str = None) -> int:
        """Apply AI-filled corrections from temp JSON to SRT.

        Reads temp/scans/ai_fragments_{EP}.json, applies every fragment
        with a non-empty 'correction' field to SRT.  Skipped fragments
        (correction left blank) are escalated via VAD check.

        Returns count of applied corrections.
        """
        if json_path is None:
            json_path = os.path.join(self._temp_dir,
                                     f'ai_fragments_{self.episode}.json')
        if not os.path.exists(json_path):
            print(f'[apply-ai] JSON not found: {json_path}', file=sys.stderr)
            return 0

        with open(json_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)

        fragments = data.get('fragments', [])
        if not fragments:
            print(f'[apply-ai] No fragments in JSON', file=sys.stderr)
            return 0

        if not self._srt_path or not os.path.exists(self._srt_path):
            print(f'[apply-ai] SRT not found for {self.episode}', file=sys.stderr)
            return 0

        cues = parse_srt(self._srt_path, mark_garbled=False)
        speech_segs = self._load_speech_segs()
        review_clusters = self._load_review_clusters()
        applied = 0
        unfilled = []

        for frag in fragments:
            timecode = frag.get('start', '')
            correction = frag.get('correction', '').strip()

            if not correction:
                # Escalate unfilled → VAD check below
                unfilled.append({
                    'ep': self.episode, 'time': timecode,
                    'original': frag.get('original', ''),
                    'context_before': frag.get('context_before', []),
                    'context_after': frag.get('context_after', []),
                })
                continue

            if correction == '删除':
                target_idx = self._find_cue_index(cues, timecode)
                if target_idx is None:
                    print(f'[apply-ai] Cue not found for delete: {timecode}',
                          file=sys.stderr)
                    continue
                removed = cues.pop(target_idx)
                print(f'[apply-ai] Deleted: {timecode} '
                      f'"{removed["text"][:60]}"', file=sys.stderr)
                applied += 1
                continue

            target_idx = self._find_cue_index(cues, timecode)
            if target_idx is None:
                print(f'[apply-ai] Cue not found: {timecode} — may be fixed',
                      file=sys.stderr)
                continue

            cluster = self._find_cluster_for_timecode(review_clusters, timecode)
            if cluster and speech_segs and self._video_path:
                aligned = self._vad_align_correction(
                    cluster, speech_segs, correction, cues[target_idx])
                if aligned:
                    cues[target_idx]['start'] = aligned['start']
                    cues[target_idx]['end'] = aligned['end']
                    cues[target_idx]['text'] = aligned['text']
                    tag = '[VAD]'
                else:
                    cues[target_idx]['text'] = correction
                    tag = '[text-only]'
            else:
                cues[target_idx]['text'] = correction
                tag = '[text-only]'

            print(f'[apply-ai] {tag} {timecode} → "{correction[:60]}"',
                  file=sys.stderr)
            applied += 1

            # Paired mode: apply corrections to neighbor cues too
            if frag.get('mode') == 'paired':
                for pc in frag.get('paired_cues', []):
                    if pc.get('role') == 'target':
                        continue
                    pc_corr = pc.get('correction', '').strip()
                    if not pc_corr:
                        continue
                    pc_start = pc.get('start', '')
                    pc_idx = self._find_cue_index(cues, pc_start)
                    if pc_idx is None:
                        continue
                    if pc_corr == '__DELETE__':
                        cues.pop(pc_idx)
                        applied += 1
                    else:
                        cues[pc_idx]['text'] = pc_corr
                        applied += 1

            # Update report: mark Layer 2.5 entry as fixed
            try:
                ok = update_entry_status(self._report_path, step='2.5',
                                    ep=self.episode, time=timecode,
                                    corrected=correction, status='✅')
                if not ok:
                    print(f'[apply-ai] Report entry not found for {self.episode} '
                          f'{timecode} — may be in another step', file=sys.stderr)
            except Exception as e:
                print(f'[apply-ai] Report update failed: {e}', file=sys.stderr)

        if applied > 0:
            write_srt(self._srt_path, cues)
            print(f'[apply-ai] {applied} corrections written to '
                  f'{os.path.basename(self._srt_path)}', file=sys.stderr)

        # ── VAD check for unfilled fragments ──
        auto_cut = 0
        escalated = []
        if unfilled and speech_segs:
            for entry in unfilled:
                ts = entry['time']
                start_s = to_seconds(ts) if ts else 0
                # Re-check original text: Latin-only noise that Whisper
                # hallucinated onto should be auto-cut, not escalated.
                original = entry.get('original', '')
                if meaningful_char_count(original, self.target_lang) < 2:
                    cues = [c for c in cues
                            if c.get('start', '') != ts]
                    auto_cut += 1
                    try:
                        update_entry_status(self._report_path, step='2.5',
                                            ep=self.episode, time=ts,
                                            corrected='(原文无语义)', status='🗑️')
                    except Exception as e:
                        print(f'[apply-ai] Report update failed (no-semantics): {e}',
                              file=sys.stderr)
                    continue
                has_speech = any(
                    es >= start_s and ss <= start_s + 5.0
                    for ss, es in speech_segs
                )
                if has_speech:
                    # ── Noise-context check: if surrounding cues are mostly
                    #     non-verbal (extended vowels, grunts, single kana),
                    #     auto-cut even though VAD detected sound — it's just
                    #     shouting/commotion, not meaningful dialogue.
                    ctx_before = entry.get('context_before', [])
                    ctx_after = entry.get('context_after', [])
                    ctx_cues = ctx_before + ctx_after
                    if ctx_cues:
                        noise_count = sum(
                            1 for t in ctx_cues
                            if meaningful_char_count(t, self.target_lang) < 2
                        )
                        if noise_count / len(ctx_cues) >= 0.6:
                            cues = [c for c in cues
                                    if c.get('start', '') != ts]
                            auto_cut += 1
                            try:
                                update_entry_status(
                                    self._report_path, step='2.5',
                                    ep=self.episode, time=ts,
                                    corrected='(噪音包围)', status='🗑️')
                            except Exception as e:
                                print(f'[apply-ai] Report update failed (noise): {e}',
                                      file=sys.stderr)
                            continue
                    escalated.append(entry)
                else:
                    cues = [c for c in cues
                            if c.get('start', '') != ts]
                    auto_cut += 1
                    # Report: mark as deleted in L2.5 (VAD confirmed no speech)
                    try:
                        update_entry_status(self._report_path, step='2.5',
                                            ep=self.episode, time=ts,
                                            corrected='(VAD无语音)', status='🗑️')
                    except Exception as e:
                        print(f'[apply-ai] Report update failed (VAD): {e}',
                              file=sys.stderr)
            if auto_cut > 0:
                write_srt(self._srt_path, cues)

        if escalated:
            pending_path = os.path.join(self._temp_dir,
                                        f'{self.episode}_pending_human.json')
            os.makedirs(self._temp_dir, exist_ok=True)
            with open(pending_path, 'w', encoding='utf-8') as f:
                json.dump(escalated, f, ensure_ascii=False, indent=2)
            # Report: move from L2.5 to L6 for human review
            try:
                for entry in escalated:
                    ts = entry['time']
                    update_entry_status(self._report_path, step='2.5',
                                        ep=self.episode, time=ts,
                                        corrected='', status='🗑️')
                    upsert_entries(self._report_path, step='6', entries=[{
                        'ep': self.episode, 'time': ts,
                        'original': entry.get('original', '')[:120],
                        'corrected': '', 'status': '⬜',
                    }])
            except Exception:
                pass
            print(f'[apply-ai] {len(escalated)} escalated to human, '
                  f'{auto_cut} auto-cut', file=sys.stderr)

        # Cleanup: remove the temp JSON
        try:
            os.remove(json_path)
        except OSError:
            pass

        return applied

    def apply(self, checklist_path: str) -> int:
        """Apply corrections from a filled checklist.

        For each entry:
        - '修正:' = '删除' → remove cue from SRT
        - '修正:' = text → VAD time-align → replace in SRT
        - '修正:' = empty → skip

        Returns count of applied corrections.
        Idempotent: only processes entries with filled 修正: fields.
        """
        if not os.path.exists(checklist_path):
            print(f'[apply] Checklist not found: {checklist_path}', file=sys.stderr)
            return 0

        if not self._srt_path or not os.path.exists(self._srt_path):
            print(f'[apply] SRT not found for {self.episode}', file=sys.stderr)
            return 0

        # Parse checklist
        corrections = self._parse_checklist(checklist_path)
        if not corrections:
            print(f'[apply] No corrections found in checklist', file=sys.stderr)
            return 0

        # Load current SRT and pre-compute VAD / cluster data
        cues = parse_srt(self._srt_path, mark_garbled=False)
        speech_segs = self._load_speech_segs()
        review_clusters = self._load_review_clusters()
        applied = 0

        for corr in corrections:
            timecode = corr['time']
            text = corr['text']  # may be '删除' or empty or corrected text

            if not text or not text.strip():
                continue  # Empty → skip

            if text.strip() == '删除':
                # Find and remove cue
                target_idx = self._find_cue_index(cues, timecode)
                if target_idx is None:
                    print(f'[apply] Cue not found for delete: {timecode}',
                          file=sys.stderr)
                    continue
                removed = cues.pop(target_idx)
                print(f'[apply] Deleted: {timecode} "{removed["text"][:60]}"',
                      file=sys.stderr)
                applied += 1
                continue

            # ── VAD time-alignment ──
            target_idx = self._find_cue_index(cues, timecode)
            if target_idx is None:
                print(f'[apply] Cue not found: {timecode} — may already be fixed',
                      file=sys.stderr)
                continue

            corrected_text = text.strip()
            cluster = self._find_cluster_for_timecode(review_clusters, timecode)

            if cluster and speech_segs and self._video_path:
                # Try VAD alignment within the cluster
                aligned = self._vad_align_correction(
                    cluster, speech_segs, corrected_text,
                    cues[target_idx])
                if aligned:
                    cues[target_idx]['start'] = aligned['start']
                    cues[target_idx]['end'] = aligned['end']
                    cues[target_idx]['text'] = aligned['text']
                    tag = '[VAD]'
                else:
                    # Fallback: keep original boundaries
                    cues[target_idx]['text'] = corrected_text
                    tag = '[fallback:orig]'
            else:
                # No cluster/VAD data → simple text replace
                cues[target_idx]['text'] = corrected_text
                tag = '[text-only]'

            print(f'[apply] {tag} {timecode} → "{corrected_text[:60]}"',
                  file=sys.stderr)
            applied += 1

        # Write back SRT
        if applied > 0:
            write_srt(self._srt_path, cues)
            print(f'[apply] {applied} corrections written to '
                  f'{os.path.basename(self._srt_path)}', file=sys.stderr)

        return applied

    def _find_cue_index(self, cues, timecode):
        """Find cue index by start timecode (tolerant of comma/dot)."""
        tc = timecode.replace(',', '.').replace('。', '.')
        for i, cue in enumerate(cues):
            ct = cue.get('start', '').replace(',', '.').replace('。', '.')
            if ct == tc:
                return i
        return None

    def _find_cluster_for_timecode(self, clusters, timecode):
        """Find the review cluster containing a given cue timecode."""
        if not clusters:
            return None
        tc = timecode.replace(',', '.').replace('。', '.')
        for cl in clusters:
            for g in cl.get('garbled', []):
                gt = g.get('start', '').replace(',', '.').replace('。', '.')
                if gt == tc:
                    return cl
        return None

    def _vad_align_correction(self, cluster, speech_segs, corrected_text, cue):
        """Use VAD to find precise speech boundaries for corrected text.

        Returns dict {start, end, text} if alignment succeeds, None if fallback needed.
        """
        # Find speech segments that overlap with this cluster
        cluster_ss = cluster['ss']
        cluster_es = cluster['es']
        overlapping = []
        for ss, es in speech_segs:
            overlap = min(es, cluster_es) - max(ss, cluster_ss)
            if overlap > 0.3:  # at least 300ms overlap
                overlapping.append((ss, es))

        n = len(overlapping)
        n_garbled = len(cluster.get('garbled', []))

        if n == 0:
            print(f'[VAD] 0 speech segments in cluster → likely non-speech, '
                  f'keeping original boundaries', file=sys.stderr)
            return None  # Fallback: keep original boundaries

        elif n == 1:
            # Perfect: single speech segment → use VAD boundaries
            ss, es = overlapping[0]
            return {
                'start': format_tc(ss),
                'end': format_tc(es),
                'text': corrected_text,
            }

        elif n == n_garbled:
            # VAD segments match garbled cue count → use corresponding segment
            # Find which garbled cue we're fixing
            cue_start_s = cue.get('start_s', 0)
            garbled_list = cluster.get('garbled', [])
            g_idx = None
            for i, g in enumerate(garbled_list):
                if abs(g.get('start_s', 0) - cue_start_s) < 0.5:
                    g_idx = i
                    break
            if g_idx is not None and g_idx < len(overlapping):
                ss, es = overlapping[g_idx]
                return {
                    'start': format_tc(ss),
                    'end': format_tc(es),
                    'text': corrected_text,
                }
            else:
                print(f'[VAD] {n} segments = garbled count but '
                      f'could not match cue → fallback', file=sys.stderr)
                return None

        else:
            print(f'[VAD] {n} speech segments ≠ {n_garbled} garbled cues '
                  f'→ fallback (keep original boundaries)', file=sys.stderr)
            return None

    def _compute_review_clip_bounds(self, garbled_start, garbled_end,
                                     cluster_ss, cluster_es, max_pad=2.0):
        """Compute video clip boundaries for human review — VAD-aware v2.

        Returns (clip_start_s, clip_end_s) or None if no speech detected.
        None = auto-cut (don't generate clip, mark as resolved).
        """
        speech_segs = self._load_speech_segs()
        if not speech_segs:
            return (max(cluster_ss, garbled_start - 0.5),
                    min(cluster_es, garbled_end + 0.5))

        # ── 1. Speech overlap with garbled cues ──
        garbled_speech_dur = 0.0
        for ss, es in speech_segs:
            overlap = min(es, garbled_end) - max(ss, garbled_start)
            if overlap > 0:
                garbled_speech_dur += overlap

        if garbled_speech_dur <= 0:
            return None

        # ── 2. Initial: garbled ± max_pad ──
        clip_start = max(cluster_ss, garbled_start - max_pad)
        clip_end = min(cluster_es, garbled_end + max_pad)

        # ── 3. Snap to VAD boundaries (capped) ──
        # Only extend if a speech segment overlaps the garbled range
        # AND its edge is within max_pad of the garbled boundary.
        # We do NOT pull in a 20s speech segment that starts far
        # before the garbled cue — that's unrelated dialogue.
        for ss, es in speech_segs:
            if es >= garbled_start and ss <= garbled_end:
                if ss < garbled_start and garbled_start - ss <= max_pad:
                    clip_start = min(clip_start, ss)
                if es > garbled_end and es - garbled_end <= max_pad:
                    clip_end = max(clip_end, es)
        clip_start = max(cluster_ss, clip_start)
        clip_end = min(cluster_es, clip_end)

        # ── 4. Trim long silences at edges ──
        clip_start = self._trim_leading_silence(
            clip_start, clip_end, max_pad, speech_segs)
        clip_end = self._trim_trailing_silence(
            clip_start, clip_end, max_pad, speech_segs)

        return clip_start, clip_end

    @staticmethod
    def _trim_leading_silence(start_s, end_s, max_pad, speech_segs):
        """Advance start_s if there is > max_pad of silence before first speech."""
        first_speech = None
        for ss, es in speech_segs:
            if es >= start_s:
                first_speech = ss
                break
        if first_speech is not None and first_speech > start_s:
            silence = first_speech - start_s
            if silence > max_pad:
                return first_speech - max_pad
        return start_s

    @staticmethod
    def _trim_trailing_silence(start_s, end_s, max_pad, speech_segs):
        """Pull back end_s if there is > max_pad of silence after last speech."""
        last_speech = None
        for ss, es in reversed(speech_segs):
            if ss <= end_s:
                last_speech = es
                break
        if last_speech is not None and last_speech < end_s:
            silence = end_s - last_speech
            if silence > max_pad:
                return last_speech + max_pad
        return end_s

    # ═══════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════

    # ── VAD & review cluster persistence ──

    def _vad_path(self):
        """Path to per-episode VAD speech segments JSON."""
        return os.path.join(self._temp_dir, f'{self.episode}_vad.json')

    def _save_speech_segs(self, speech_segs):
        """Persist VAD speech_segs for later use (human review apply)."""
        try:
            os.makedirs(self._temp_dir, exist_ok=True)
            with open(self._vad_path(), 'w', encoding='utf-8') as f:
                json.dump({'speech_segs': speech_segs}, f, ensure_ascii=False)
        except Exception as e:
            print(f'[vad] Failed to save speech_segs: {e}', file=sys.stderr)

    def _convert_placeholders_to_markers(self) -> int:
        """Convert any remaining ⚠SPEECH placeholders in SRT to [???].

        This is a safety net — placeholders should have been handled by
        the triage (Whisper success → transcribed text; Whisper failure →
        [???]), but edge cases can leave residuals (e.g., Whisper produced
        no output at all, or a cluster failed to extract audio).

        Returns count of converted markers.
        """
        if not self._srt_path or not os.path.exists(self._srt_path):
            return 0
        try:
            with open(self._srt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            count = content.count('⚠SPEECH')
            if count > 0:
                content = content.replace('⚠SPEECH', '[???]')
                with open(self._srt_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f'[whisper] Safety scan: {count} ⚠SPEECH → [???] '
                      f'in {os.path.basename(self._srt_path)}',
                      file=sys.stderr)
            return count
        except Exception as e:
            print(f'[whisper] Safety scan failed: {e}', file=sys.stderr)
            return 0

    def _load_speech_segs(self):
        """Load persisted VAD speech_segs, or empty list."""
        path = self._vad_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f).get('speech_segs', [])
            except Exception:
                pass
        return []

    def _review_clusters_path(self):
        return os.path.join(self._temp_dir, f'{self.episode}_review_clusters.json')

    # ── Review-specific clustering (tighter than Whisper's 60s gap) ──
    REVIEW_CLUSTER_GAP = 8.0   # seconds — max gap between garbled cues in a review cluster

    def _build_review_clusters(self, cues, pending_items):
        """Build tight review clusters for human review video clips.

        KEY DIFFERENCE from Whisper's build_clusters():
        - Uses 8s gap (not 60s) — groups only nearby garbled cues
        - Does NOT expand to adjacent clean cue boundaries for clip
          extraction; _compute_review_clip_bounds() uses VAD instead
        - Still captures left/right clean cue TEXT for context display
          in the checklist (but NOT for clip time boundaries)
        - Cluster ss/es = garbled range only; VAD-aware padding is
          layered on by _compute_review_clip_bounds()
        """
        # Mark garbled cues that match pending items
        pending_starts = {e.get('time', '') for e in pending_items}
        for c in cues:
            if c.get('start', '') in pending_starts:
                c['is_garbled'] = True

        # Collect only the garbled cues we care about
        garbled_cues = [c for c in cues if c.get('is_garbled')
                       and c.get('start', '') in pending_starts]
        if not garbled_cues:
            return []

        # Per-cue clusters: each garbled cue gets its own clip.
        # No grouping — grouping nearby cues creates 30s clips full of
        # sound effects when Whisper hallucinates in quick succession.
        groups = [[g] for g in garbled_cues]

        # Build cluster dicts
        clusters = []
        for g_group in groups:
            first, last = g_group[0], g_group[-1]

            # Find adjacent clean cues for TEXT CONTEXT only
            # (not for clip boundaries — VAD handles that)
            first_idx = next((k for k, c in enumerate(cues)
                            if c['start'] == first['start']), 0)
            last_idx = next((k for k, c in enumerate(cues)
                           if c['start'] == last['start']), 0)

            left = next((cues[k] for k in range(first_idx - 1, -1, -1)
                        if not cues[k].get('is_garbled') and cues[k]['text']), None)
            right = next((cues[k] for k in range(last_idx + 1, len(cues))
                         if not cues[k].get('is_garbled') and cues[k]['text']), None)

            # Cluster boundaries: start from the garbled cues, then add
            # generous headroom so _compute_review_clip_bounds can find
            # nearby speech.  Edge-truncation there will tighten the final
            # clip to ≤ 5 s silence on each side.
            REVIEW_SEARCH_WINDOW = 5.0   # per-cue: tight window
            ss = max(0, first['start_s'] - REVIEW_SEARCH_WINDOW)
            es = last['end_s'] + REVIEW_SEARCH_WINDOW

            clusters.append({
                'ss': ss, 'es': es,
                'dur': es - ss,
                'garbled': g_group,
                'left_text': left['text'] if left else '',
                'right_text': right['text'] if right else '',
            })
        return clusters

    def _save_review_clusters(self, clusters):
        """Save cluster info as JSON for apply() to use in VAD alignment."""
        try:
            os.makedirs(self._temp_dir, exist_ok=True)
            # Strip non-serializable fields
            serializable = []
            for cl in clusters:
                serializable.append({
                    'ss': cl['ss'], 'es': cl['es'],
                    'garbled': [{'start': g['start'], 'end': g['end'],
                                 'text': g['text'][:80],
                                 'start_s': g['start_s'], 'end_s': g['end_s']}
                                for g in cl['garbled']],
                    'left_text': cl.get('left_text', ''),
                    'right_text': cl.get('right_text', ''),
                })
            with open(self._review_clusters_path(), 'w', encoding='utf-8') as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[review] Failed to save clusters: {e}', file=sys.stderr)

    def _load_review_clusters(self):
        """Load persisted review clusters."""
        path = self._review_clusters_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _find_adjacent_cues(self, cues: list, timecode: str):
        """Find clean cues immediately before and after a given timecode.

        Used for context wrapping in video clip extraction.
        Returns (left_cue, right_cue), either may be None.
        """
        target_s = to_seconds(timecode) if timecode else 0
        left, right = None, None

        sorted_cues = sorted(cues, key=lambda c: c['start_s'])

        for cue in sorted_cues:
            if cue['end_s'] <= target_s:
                # Cue ends before target → candidate for left
                if not cue.get('is_garbled') and cue.get('text'):
                    left = cue
            elif cue['start_s'] >= target_s + 5.0:  # at least 5s after target
                # Cue starts well after target → candidate for right
                if not cue.get('is_garbled') and cue.get('text'):
                    right = cue
                    break

        return left, right

    def _extract_clip(self, video_path: str, start_s: float,
                      end_s: float, output_path: str) -> bool:
        """Extract a video clip using ffmpeg. Returns True on success."""
        clip_start = max(0, start_s)
        duration = end_s - start_s
        # Floor at 2s (minimum watchable), reasonable ceiling at 60s
        # (bounds are now VAD-aware so most clips stay < 25s;
        #  the cap is a safety net for pathological edge cases)
        duration = max(2.0, min(duration, 60.0))

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-ss', str(clip_start),
            '-t', str(duration),
            '-i', video_path,
            '-c:v', 'libx264', '-crf', '28',
            '-c:a', 'aac', '-b:a', '64k',
            '-vf', 'scale=640:-2',
            '-movflags', '+faststart',
            output_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=120)
            return True
        except subprocess.TimeoutExpired:
            print(f'  TIMEOUT: {output_path}', file=sys.stderr)
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b'')[-200:]
            print(f'  FFMPEG ERROR: {err}', file=sys.stderr)
        except Exception as e:
            print(f'  ERROR: {e}', file=sys.stderr)
        return False

    def _parse_checklist(self, path: str) -> list:
        """Parse a filled review-checklist.md into a list of corrections.

        Each correction: {'time': '00:02:00.000', 'text': '正しいテキスト'}
        text may be '删除', empty, or the corrected subtitle text.
        """
        if not os.path.exists(path):
            return []

        # Check version
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'version: 2' not in content:
            print('[apply] Old checklist format detected — ignoring.'
                  ' Re-run Fixer.review() to generate a new one.',
                  file=sys.stderr)
            return []

        corrections = []
        # Parse entries: each entry is separated by "---"
        blocks = re.split(r'\n---\n', content)

        for block in blocks:
            # Extract timecode from header line:
            # "EP002 | 00:21:18.029 ~ 00:21:20.429 | EP002_00-21-18-029.mp4"
            time_match = re.search(
                r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*~\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})?',
                block)
            if not time_match:
                continue

            timecode = time_match.group(1).replace(',', '.')

            # Skip already-processed entries (marked as ✅)
            if re.search(r'^\s*✅', block, re.MULTILINE):
                continue

            # Extract correction text after "修正:"
            corr_match = re.search(r'修正:\s*\n?(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
            if not corr_match:
                continue

            text = corr_match.group(1).strip()

            corrections.append({
                'time': timecode,
                'text': text,
            })

        return corrections



# ═══════════════════════════════════════════════════════════════
# CLI (for standalone debugging)
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Fix orchestrator — unified error-fix pipeline')
    parser.add_argument('episode', help='Episode ID (e.g., EP002)')
    parser.add_argument('--project-dir', default=os.getcwd(),
                        help='Project root directory')
    parser.add_argument('--target-lang', default='ja',
                        help='Target language (ja|zh)')
    parser.add_argument('--step', choices=['check', 'whisper', 'review', 'apply'],
                        help='Run a specific step')
    parser.add_argument('--checklist', help='Checklist path for --step apply')
    parser.add_argument('--reference', help='Reference SRT path for --step reference')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    fixer = Fixer(args.episode, args.project_dir,
                  target_lang=args.target_lang)

    if args.step == 'check':
        clean = fixer.is_clean()
        count = fixer.problem_count()
        print(f'{args.episode}: {"clean" if clean else f"{count} garbled cues"}')

    elif args.step == 'whisper':
        if args.dry_run:
            print(f'[DRY RUN] Would run Whisper on {args.episode}')
        else:
            fixer.fix_by_whisper()

    elif args.step == 'review':
        fixer.review()

    elif args.step == 'apply':
        if not args.checklist:
            print('ERROR: --checklist required for --step apply')
            sys.exit(1)
        applied = fixer.apply(args.checklist)
        print(f'Applied: {applied}')

    else:
        # Default: run_auto
        if args.dry_run:
            clean = fixer.is_clean()
            print(f'{args.episode}: {"clean — nothing to do" if clean else "would run auto-fix"}')
        else:
            fixer.run_auto()
