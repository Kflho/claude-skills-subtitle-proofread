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
    get_audio_duration, meaningful_jp_count, is_length_anomaly,
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
# Fixer
# ═══════════════════════════════════════════════════════════════

# Default Whisper paths — read from environment variables.
# Set WHISPER_CLI, WHISPER_MODEL, and WHISPER_RETRY_MODEL in your shell
# or pass whisper_cli=/model=/retry_model= to the Fixer constructor.
DEFAULT_WHISPER_CLI = os.environ.get('WHISPER_CLI', '')
DEFAULT_WHISPER_MODEL = os.environ.get('WHISPER_MODEL', '')
DEFAULT_RETRY_MODEL = os.environ.get('WHISPER_RETRY_MODEL', '')


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
                 retry_model: str = None):
        """Create a fix session for one episode.

        Args:
            episode: 'EP002'
            project_dir: project root (contains AI审查后/, temp/, reports/)
            target_lang: target language 'ja' | 'zh'
            video_dir: video files directory (auto-detected if None)
            whisper_cli: path to whisper-cli.exe
            model: path to main Whisper model
            retry_model: path to fallback Whisper model
        """
        self.episode = episode
        self.project_dir = project_dir
        self.target_lang = target_lang

        # Paths
        self._srt_dir = os.path.join(project_dir, 'AI审查后')
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

        self._resolve_paths()

    # ── Path resolution ──

    def _resolve_paths(self):
        """Find SRT and video files for this episode."""
        ep_num = self.episode[2:]  # '064' from 'EP064'

        # Find SRT
        if os.path.isdir(self._srt_dir):
            for fname in sorted(os.listdir(self._srt_dir)):
                if fname.endswith('.srt') and ep_num in fname:
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
                if ep_num in fname and fname.lower().endswith(exts):
                    self._video_path = os.path.join(vdir, fname)
                    self._video_dir = vdir
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
                       skip_vad_clean: bool = False) -> FixReport:
        """Run Whisper auto-fix pipeline.

        1. VAD clean: delete non-speech cues
        2. Build clusters with context wrapping
        3. Tier 1 (segment-based) → auto-upgrade to Tier 2 if needed
        4. Apply fixes to SRT
        5. Update report: Layer 2 ✅ for fixed, Layer 6 ⬜ for unfixable

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

            # Step 1: VAD clean
            speech_segs = []
            if not skip_vad_clean:
                from fix.whisper_pipeline import vad_delete_nonspeech
                vad_audio = os.path.join(tmpdir, 'vad_full.wav')
                try:
                    extract_audio_wav(self._video_path, vad_audio)
                    cues, deleted, speech_segs = vad_delete_nonspeech(
                        vad_audio, cues, self._srt_path)
                    deleted_count = len(deleted)
                    # Persist speech_segs for later use (human review VAD alignment)
                    self._save_speech_segs(speech_segs)
                except Exception as e:
                    print(f'[whisper] VAD audio extraction failed: {e}',
                          file=sys.stderr)
                    print(f'[whisper] Continuing without VAD clean',
                          file=sys.stderr)

            # Reload garbled after VAD
            garbled = [c for c in cues if c.get('is_garbled')]
            if not garbled:
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

            from lib.whisper_utils import looks_like_plausible_japanese

            auto_keep = []
            ai_fragments = []        # → L2.5 AI上下文补全
            auto_cut = []            # → 直接删除 (meaningful_jp < 2)

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

            for f in all_items:
                # eval_text: prefer Whisper output, fall back to original
                eval_text = (f.get('replacement') or f.get('original', '')).strip()

                # ① Pre-filter: no meaningful Japanese → auto-cut
                if meaningful_jp_count(eval_text) < 2:
                    auto_cut.append(f)
                    continue

                # ② Readable Japanese → auto-keep (unless hallucination-suspect)
                if looks_like_plausible_japanese(eval_text, self.target_lang):
                    original = f.get('original', '')
                    # Only check length anomaly if the ORIGINAL had meaningful
                    # Japanese content.  Pure noise (Latin, single kana) has
                    # nothing to preserve — Whisper output is always an improvement.
                    if (f.get('replacement')
                            and meaningful_jp_count(original) >= 2
                            and is_length_anomaly(original, eval_text)):
                        ai_fragments.append(f)
                    else:
                        auto_keep.append(f)
                    continue

                # ③ Has Japanese content but with Latin corruption → AI completion
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

            report.applied = len(auto_keep)
            report.ai_review = len(ai_fragments)
            report.failed = 0   # nothing goes directly to L6 anymore
            report.details = []

            # ── Write to 问题解决报告 ──
            try:
                if auto_keep:
                    upsert_entries(self._report_path, step='2', entries=[
                        {'ep': self.episode, 'time': f.get('start', ''),
                         'original': f.get('original', '')[:120],
                         'corrected': f.get('replacement', '')[:120],
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
                    upsert_entries(self._report_path, step='2.5', entries=[
                        {'ep': self.episode, 'time': f.get('start', ''),
                         'original': f.get('original', '')[:120],
                         'corrected': f.get('replacement', '')[:120],
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
    # Cascading auto-fix
    # ═══════════════════════════════════════════════════════════

    def run_auto(self) -> FixReport:
        """Run cascading auto-fix: reference → whisper → human.

        Priority is hard-coded, not configurable:
        1. If reference SRT exists in 参考字幕/ → fix_by_reference
        2. For remaining garbled cues → fix_by_whisper
        3. For still unfixable → review (generate checklist)

        Returns combined FixReport.
        """
        if self.is_clean():
            print(f'[{self.episode}] ✓ clean — nothing to fix', file=sys.stderr)
            return FixReport(source='auto')

        report = FixReport(source='auto')

        # Priority 1: Reference comparison
        ref_dir = os.path.join(self.project_dir, '参考字幕')
        ref_srt = None
        if os.path.isdir(ref_dir):
            ep_num = self.episode[2:]
            for fname in os.listdir(ref_dir):
                if fname.endswith('.srt') and ep_num in fname:
                    ref_srt = os.path.join(ref_dir, fname)
                    break

        if ref_srt:
            # Translate reference first (external step — whole file)
            translated_dir = os.path.join(self.project_dir, 'temp', 'translations')
            os.makedirs(translated_dir, exist_ok=True)
            translated_srt = os.path.join(
                translated_dir, f'{self.episode}_translated.srt')

            # Only translate if not already done
            if not os.path.exists(translated_srt):
                print(f'[{self.episode}] Translating reference SRT ...',
                      file=sys.stderr)
                translate_script = os.path.join(_SCRIPT_DIR, 'translate_srt.py')
                try:
                    env = os.environ.copy()
                    if _SCRIPT_DIR not in env.get('PYTHONPATH', ''):
                        env['PYTHONPATH'] = _SCRIPTS_DIR + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
                    subprocess.run([
                        sys.executable, translate_script,
                        ref_srt, '--to', self.target_lang,
                        '--output', translated_srt,
                    ], capture_output=False, timeout=1800, check=False, env=env)
                except Exception as e:
                    print(f'[{self.episode}] Translation failed: {e}',
                          file=sys.stderr)
                    translated_srt = None

            if translated_srt and os.path.exists(translated_srt):
                ref_result = self.fix_by_reference(translated_srt)
                report.merge(ref_result)

        # Priority 2: Whisper for remaining garbled
        if not self.is_clean() and self._video_path:
            whisper_result = self.fix_by_whisper()
            report.merge(whisper_result)

        # Human review checklist is generated later by run_all.py:step_deliver().
        # Don't generate it here — that would create a duplicate flat-file
        # checklist alongside the per-ep-folder one.

        print(f'\n[{self.episode}] Auto-fix complete: '
              f'{report.applied} applied, {report.failed} → manual review',
              file=sys.stderr)
        return report

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
                if not os.path.exists(clip_path) and self._video_path:
                    ok = self._extract_clip(self._video_path, clip_start,
                                            clip_end, clip_path)
                    if ok:
                        extracted += 1
                    else:
                        skipped += 1
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

            # Find adjacent clean cues for context
            start_s = to_seconds(start_ts) if start_ts else 0
            end_s = to_seconds(end_ts) if end_ts else start_s + 5.0

            context_before = []
            context_after = []
            for cue in sorted(cues, key=lambda c: c['start_s']):
                cs = cue.get('start_s', 0)
                ct = cue.get('text', '').strip()
                if ct and not cue.get('is_garbled'):
                    if cs < start_s - 1:
                        context_before.append(ct)
                        if len(context_before) > 2:
                            context_before.pop(0)
                    elif cs > end_s + 1 and len(context_after) < 2:
                        context_after.append(ct)

            entry = {
                'start': start_ts,
                'end': end_ts,
                'original': original[:200],
                'whisper_attempt': corrected,
                'context_before': context_before,
                'context_after': context_after,
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
                update_entry_status(self._report_path, step='2.5',
                                    ep=self.episode, time=timecode,
                                    corrected=correction, status='✅')
            except Exception:
                pass

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
                if meaningful_jp_count(original) < 2:
                    cues = [c for c in cues
                            if c.get('start', '') != ts]
                    auto_cut += 1
                    try:
                        update_entry_status(self._report_path, step='2.5',
                                            ep=self.episode, time=ts,
                                            corrected='(原文无语义)', status='🗑️')
                    except Exception:
                        pass
                    continue
                has_speech = any(
                    es >= start_s and ss <= start_s + 5.0
                    for ss, es in speech_segs
                )
                if has_speech:
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
                    except Exception:
                        pass
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
