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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path as _Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from lib.whisper_utils import (
    setup_windows_utf8, extract_ep_number, to_seconds, format_tc,
    parse_srt, write_srt, apply_fixes_to_srt, run_whisper,
    extract_audio_wav, is_valid_japanese, classify_garbled_text,
    get_audio_duration,
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

# Default Whisper paths (can be overridden in constructor)
DEFAULT_WHISPER_CLI = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe'
DEFAULT_WHISPER_MODEL = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-kotoba-whisper-v2.0-q5_0.bin'
DEFAULT_RETRY_MODEL = r'D:/software/video/whisper-cublas-12.4.0-bin-x64/models/ggml-large-v3-q5_0.bin'

CHECKLIST_HEADER = """# 人工审查清单 — {episode}
> 导出: {date}
> 共 {count} 条待审查
>
> version: 2
>
> **填写方法**：每条下方「修正:」后直接写正确台词。
> 填「删除」则从 SRT 移除该 cue。
>
> 填写完毕后运行:
>   python scripts/run_all.py --apply-checklist
>
---
"""

CHECKLIST_ENTRY = """{ep} | {start} ~ {end} | {clip_file}
来源: {source}
残留: {original}
修正:

---
"""


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

        # Log to report
        self._upsert_layer('2', [
            {'ep': self.episode, 'time': f['start'],
             'original': f['original'], 'corrected': f['replacement'],
             'status': '✅'}
            for f in fixes
        ])
        if unfixable:
            self._upsert_layer('6', unfixable)

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
            return FixReport(source='whisper', failed=0,
                             details=['No video file found'])

        if not self._srt_path:
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

            # Step 3: Apply fixes to SRT
            applied_fixes = [f for f in fixes if f.get('replacement')]
            if applied_fixes:
                apply_fixes_to_srt(self._srt_path, applied_fixes)

            # Step 4: Build report with readability-first auto_triage
            report = FixReport(source='whisper', tier=tier,
                              deleted=deleted_count)

            from lib.whisper_utils import (
                looks_like_plausible_japanese,
                is_short_garbled_fragment,
                is_proper_noun_pattern,
            )

            auto_keep = []
            ai_short_fragments = []   # → L2.5 AI上下文补全
            proper_noun_items = []    # → L3 专名审查
            human_items = []          # → L6 人工

            for f in fixes:
                confidence = f.get('confidence', 'none')
                replacement = f.get('replacement', '')
                original = f.get('original', '')

                # Unfixable → human
                if confidence == 'none' or not replacement:
                    human_items.append(f)
                    continue

                # Readability-first: looks like Japanese → keep immediately
                if looks_like_plausible_japanese(replacement, self.target_lang):
                    auto_keep.append(f)
                    continue

                # Not readable → classify
                if is_proper_noun_pattern(original):
                    proper_noun_items.append(f)
                elif is_short_garbled_fragment(replacement, self.target_lang):
                    ai_short_fragments.append(f)
                else:
                    human_items.append(f)

            # Fallback: if Whisper produced no fixes but garbled cues remain
            # (e.g. single isolated cue, no audio output), route to human review.
            if not fixes and garbled:
                print(f'[whisper] No Whisper output — {len(garbled)} garbled '
                      f'cue(s) → L6 human review', file=sys.stderr)
                for g in garbled:
                    human_items.append({
                        'start': g['start'], 'end': g['end'],
                        'original': g['text'], 'replacement': None,
                        'confidence': 'none', 'model': 'tier1',
                    })

            # Layer 2: auto-kept (readable) → ✅
            if auto_keep:
                self._upsert_layer('2', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': f.get('replacement', '')[:80],
                     'status': '✅'}
                    for f in auto_keep
                ])

            # Layer 2.5: short fragments → AI completion
            if ai_short_fragments:
                self._upsert_layer('2.5', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': f.get('replacement', '')[:80],
                     'status': '⬜'}
                    for f in ai_short_fragments
                ])

            # Layer 3: proper noun pattern → noun pipeline
            if proper_noun_items:
                self._upsert_layer('3', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': f.get('replacement', '')[:80],
                     'status': '⬜'}
                    for f in proper_noun_items
                ])

            # Layer 6: unfixable/long-garbled → human
            if human_items:
                self._upsert_layer('6', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': '',
                     'status': '⬜'}
                    for f in human_items
                ])

            report.applied = len(auto_keep)
            report.failed = len(human_items)
            report.ai_review = len(ai_short_fragments)
            report.details = []

            print(f'[whisper] {report.applied} auto-keep, '
                  f'{report.ai_review} AI complete, '
                  f'{len(proper_noun_items)} → L3, '
                  f'{report.failed} → L6', file=sys.stderr)

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
                    subprocess.run([
                        sys.executable, translate_script,
                        ref_srt, '--to', self.target_lang,
                        '--output', translated_srt,
                    ], capture_output=False, timeout=1800, check=False)
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

        # Priority 3: Human review for still unfixable
        if not self.is_clean():
            checklist = self.review()
            if checklist:
                report.details.append(f'Checklist: {checklist}')

        print(f'\n[{self.episode}] Auto-fix complete: '
              f'{report.applied} applied, {report.failed} → manual review',
              file=sys.stderr)
        return report

    # ═══════════════════════════════════════════════════════════
    # Source 3: Human review
    # ═══════════════════════════════════════════════════════════

    def review(self, output_dir: str = None) -> str | None:
        """Generate manual review checklist + video clips.

        Uses the same build_clusters() as Whisper for context wrapping
        (left clean cue → garbled cues → right clean cue).  Extracts
        video clips for human judgment (口型/场景/字幕叠加).

        Args:
            output_dir: where to write clips + checklist
                       (default: reports/manual-review/)

        Returns:
            Path to checklist.md, or None if nothing to review
        """
        out_dir = output_dir or self._review_dir

        # Read unfixable from report Layer 6
        from utils.update_report import read_report
        data = read_report(self._report_path)
        entries = data.get('6', [])
        pending = [e for e in entries
                   if e.get('status') == '⬜' and e.get('ep') == self.episode]

        if not pending:
            print(f'[{self.episode}] review: nothing pending', file=sys.stderr)
            return None

        # Load cues and build clusters (same logic as Whisper)
        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=True, target_lang=self.target_lang)
        clusters = self._build_review_clusters(cues, pending)

        if not clusters:
            print(f'[{self.episode}] review: could not build clusters',
                  file=sys.stderr)
            return None

        os.makedirs(out_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries_md = []
        extracted = 0
        skipped = 0

        for cl in clusters:
            # Use the first garbled cue's timecode as the entry key
            first_g = cl['garbled'][0]
            last_g = cl['garbled'][-1]
            start_ts = first_g['start']
            safe_start = start_ts.replace(':', '-').replace(',', '-').replace('.', '-')
            clip_name = f'{self.episode}_{safe_start}.mp4'
            clip_path = os.path.join(out_dir, clip_name)

            # ── Human review clip: garbled cue + VAD non-speech padding, max 5s ──
            # Whisper needs adjacent clean cues for acoustic context, but humans
            # only need to see/hear the garbled segment itself.  Use VAD speech_segs
            # to find non-speech padding around the garbled cues.
            garbled_start = first_g['start_s']
            garbled_end = last_g['end_s']
            clip_start, clip_end = self._compute_review_clip_bounds(
                garbled_start, garbled_end, cl.get('ss'), cl.get('es'))

            if not os.path.exists(clip_path) and self._video_path:
                ok = self._extract_clip(self._video_path, clip_start,
                                        clip_end, clip_path)
                if ok:
                    extracted += 1
                else:
                    skipped += 1
            elif os.path.exists(clip_path):
                extracted += 1

            # Build context text from cluster
            left_text = cl.get('left_text', '')
            right_text = cl.get('right_text', '')
            garbled_texts = ' | '.join(g['text'][:60] for g in cl['garbled'])

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

        # Write checklist
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

        # Save cluster info for apply() VAD alignment
        self._save_review_clusters(clusters)

        print(f'[{self.episode}] review: {len(entries_md)} entries → '
              f'{checklist_path} ({extracted} clips, {skipped} skipped)',
              file=sys.stderr)
        return checklist_path

    # ═══════════════════════════════════════════════════════════
    # Source 3b: AI short fragment completion (Layer 2.5)
    # ═══════════════════════════════════════════════════════════

    def review_ai(self, output_dir: str = None) -> str | None:
        """Generate AI-review checklist for short garbled fragments.

        Like review() but WITHOUT video clips — AI infers text from
        surrounding dialogue context rather than listening to audio.
        Reuses the same checklist markdown format so apply() works unchanged.

        Reads Layer 2.5 ⬜ entries from the report.

        Returns:
            Path to ai_review.md, or None if nothing to review
        """
        out_dir = output_dir or self._review_dir

        from utils.update_report import read_report
        data = read_report(self._report_path)
        entries = data.get('2.5', [])
        pending = [e for e in entries
                   if e.get('status') == '⬜' and e.get('ep') == self.episode]

        if not pending:
            print(f'[{self.episode}] review_ai: nothing pending',
                  file=sys.stderr)
            return None

        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=False)

        os.makedirs(out_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries_md = []

        for item in pending:
            start_ts = item.get('time', '')
            end_ts = item.get('end', '')
            original = item.get('original', '')
            corrected = item.get('corrected', '')

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
                        # Keep last 2 cues before target
                        context_before.append(ct)
                        if len(context_before) > 2:
                            context_before.pop(0)
                    elif cs > end_s + 1 and len(context_after) < 2:
                        context_after.append(ct)

            safe_start = start_ts.replace(':', '-').replace(',', '-').replace('.', '-')

            entries_md.append(
                f'{self.episode} | {start_ts} ~ {end_ts or "?"}\n'
                f'来源: short garbled fragment\n'
                f'残留: {original}\n'
                f'Whisper尝试: {corrected}\n'
                f'上文: {"  |  ".join(context_before) if context_before else "(无)"}\n'
                f'下文: {"  |  ".join(context_after) if context_after else "(无)"}\n'
                f'修正:\n'
                f'\n---\n'
            )

        checklist_path = os.path.join(out_dir,
                                       f'{self.episode}_ai_review.md')
        header = (f'# AI 短碎片补全清单 — {self.episode}\n'
                  f'> 导出: {today}\n'
                  f'> 共 {len(entries_md)} 条\n'
                  f'> version: 2\n'
                  f'>\n'
                  f'> **AI任务**：读上下文，推测正确台词填入「修正:」后。\n'
                  f'> 不确定则留空，不要猜。格式与人工清单相同，apply() 自动应用。\n'
                  f'>\n'
                  f'---\n\n')
        with open(checklist_path, 'w', encoding='utf-8') as f:
            f.write(header)
            for entry_md in entries_md:
                f.write(entry_md)

        print(f'[{self.episode}] review_ai: {len(entries_md)} entries → '
              f'{checklist_path}', file=sys.stderr)
        return checklist_path

    def apply(self, checklist_path: str) -> int:
        """Apply human corrections from a filled checklist.

        For each ⬜ entry:
        - '修正:' = '删除' → remove cue from SRT → report ✅
        - '修正:' = text → VAD time-align → replace in SRT → report ✅
        - '修正:' = empty → skip, keep ⬜

        VAD alignment with robust fallbacks:
        1. Extract cluster audio → VAD → 1 speech segment → use VAD boundaries
        2. VAD returns 0 segments → mark as likely non-speech, suggest deletion
        3. VAD returns N≠1 segments → fallback: keep original cue time, replace text
        4. VAD unavailable (no webrtcvad / no audio) → fallback: keep original
        5. No video/cluster info → fallback: find by timecode, replace text

        Returns count of applied corrections.
        Idempotent: only processes ⬜ entries.
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
                self._mark_checklist_done(corrections, timecode, '✅')
                applied += 1
                continue

            # ── VAD time-alignment ──
            target_idx = self._find_cue_index(cues, timecode)
            if target_idx is None:
                print(f'[apply] Cue not found: {timecode} — may already be fixed',
                      file=sys.stderr)
                self._mark_checklist_done(corrections, timecode, '✅')
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
            self._mark_checklist_done(corrections, timecode, '✅')
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
                'start': self._seconds_to_tc(ss),
                'end': self._seconds_to_tc(es),
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
                    'start': self._seconds_to_tc(ss),
                    'end': self._seconds_to_tc(es),
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

    @staticmethod
    def _seconds_to_tc(seconds):
        """Convert float seconds to SRT timecode HH:MM:SS,mmm."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')

    def _compute_review_clip_bounds(self, garbled_start, garbled_end,
                                     cluster_ss, cluster_es, max_dur=5.0):
        """Compute video clip boundaries for human review.

        Unlike Whisper (needs adjacent clean cues for context), humans only
        need the garbled segment + VAD non-speech padding, capped at 5s.
        """
        speech_segs = self._load_speech_segs()
        pad_before = 0.0
        pad_after = 0.0

        if speech_segs:
            last_speech_end = None
            for ss, es in speech_segs:
                if es <= garbled_start:
                    last_speech_end = es
            if last_speech_end is not None:
                pad_before = min(garbled_start - last_speech_end, 2.0)
            else:
                pad_before = min(garbled_start - cluster_ss, 1.0)

            first_speech_after = None
            for ss, es in speech_segs:
                if ss >= garbled_end:
                    first_speech_after = ss
                    break
            if first_speech_after is not None:
                pad_after = min(first_speech_after - garbled_end, 2.0)
            else:
                pad_after = min(cluster_es - garbled_end, 1.0)
        else:
            pad_before = 0.5
            pad_after = 0.5

        clip_start = max(cluster_ss, garbled_start - pad_before)
        clip_end = min(cluster_es, garbled_end + pad_after)

        dur = clip_end - clip_start
        if dur > max_dur:
            excess = dur - max_dur
            trim_each = excess / 2.0
            clip_start = max(garbled_start - 0.3, clip_start + trim_each)
            clip_end = min(garbled_end + 0.3, clip_end - trim_each)

        return clip_start, clip_end

    # ═══════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════

    def _upsert_layer(self, step: str, entries: list):
        """Write entries to the report. Wraps update_report.upsert_entries."""
        try:
            from utils.update_report import upsert_entries
            upsert_entries(self._report_path, step=step, entries=entries)
        except Exception as e:
            print(f'[report] Failed to update Layer {step}: {e}',
                  file=sys.stderr)

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

    def _build_review_clusters(self, cues, pending_items):
        """Build review clusters using the same logic as Whisper's build_clusters().

        Groups garbled cues by proximity, expands to adjacent clean cue
        boundaries.  Returns clusters with left_text/right_text for context
        display and ss/es for video clip extraction.
        """
        from fix.whisper_pipeline import build_clusters

        # Mark garbled cues that match pending items, unmark the rest
        pending_starts = {e.get('time', '') for e in pending_items}
        for c in cues:
            if c.get('start', '') in pending_starts:
                c['is_garbled'] = True
            elif not c.get('is_garbled'):
                pass  # keep existing garbled marks

        return build_clusters(cues)

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
        duration = max(end_s - start_s, 2.0)
        duration = max(2.0, min(duration, 30.0))

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
            corr_match = re.search(r'修正:\s*\n(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
            if not corr_match:
                continue

            text = corr_match.group(1).strip()

            corrections.append({
                'time': timecode,
                'text': text,
            })

        return corrections

    def _mark_checklist_done(self, corrections: list, timecode: str,
                             status: str):
        """Update report entry for a single applied correction.

        Also updates the report Layer 6 entry from ⬜ → ✅.
        """
        try:
            from utils.update_report import update_entry_status
            update_entry_status(
                self._report_path, step='6',
                ep=self.episode, time=timecode,
                status=status,
            )
        except Exception as e:
            print(f'[report] Failed to update status: {e}', file=sys.stderr)


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
