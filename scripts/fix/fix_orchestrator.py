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

# Whisper confidence thresholds (for AI review flagging)
AI_REVIEW_AVG_LOGPROB_THRESHOLD = -1.0
AI_REVIEW_COMPRESSION_THRESHOLD = 2.0
AI_REVIEW_NO_SPEECH_THRESHOLD = 0.4

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
            if not skip_vad_clean:
                from fix.whisper_pipeline import vad_delete_nonspeech
                vad_audio = os.path.join(tmpdir, 'vad_full.wav')
                try:
                    extract_audio_wav(self._video_path, vad_audio)
                    cues, deleted, speech_segs = vad_delete_nonspeech(
                        vad_audio, cues, self._srt_path)
                    deleted_count = len(deleted)
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

            # Step 4: Build report
            report = FixReport(source='whisper', tier=tier,
                              deleted=deleted_count)

            # Separate by confidence
            fixed = [f for f in fixes
                     if f['confidence'] in ('high', 'retry')]
            unfixed = [f for f in fixes
                       if f['confidence'] == 'none']

            # Filter low-confidence for AI review
            ai_review_items = []
            for f in fixed:
                alp = f.get('avg_logprob')
                cr = f.get('compression_ratio')
                nsp = f.get('no_speech_prob', -1.0)
                if ((alp is not None and alp < AI_REVIEW_AVG_LOGPROB_THRESHOLD) or
                    (cr is not None and cr > AI_REVIEW_COMPRESSION_THRESHOLD) or
                    (nsp >= 0 and nsp > AI_REVIEW_NO_SPEECH_THRESHOLD)):
                    ai_review_items.append(f)

            # Layer 2: fixed with high confidence → ✅
            high_conf = [f for f in fixed if f not in ai_review_items]
            if high_conf:
                self._upsert_layer('2', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': f.get('replacement', '')[:80],
                     'status': '✅'}
                    for f in high_conf
                ])

            # Layer 2.5: low confidence → AI review
            if ai_review_items:
                self._upsert_layer('2.5', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': f.get('replacement', '')[:80],
                     'status': '⬜'}
                    for f in ai_review_items
                ])

            # Layer 6: unfixable → human
            if unfixed:
                self._upsert_layer('6', [
                    {'ep': self.episode, 'time': f['start'],
                     'original': f.get('original', '')[:80],
                     'corrected': '',
                     'status': '⬜'}
                    for f in unfixed
                ])

            report.applied = len(high_conf)
            report.failed = len(unfixed)
            report.ai_review = len(ai_review_items)

            print(f'[whisper] {report.applied} fixed, {report.ai_review} '
                  f'AI review, {report.failed} unfixable', file=sys.stderr)

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

        Reads Layer 6 ⬜ entries from the report. For each entry,
        extracts a video clip with context wrapping (adjacent clean
        cues as acoustic context).

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

        # Load cues for context wrapping
        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=False)

        os.makedirs(out_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries_md = []
        extracted = 0
        skipped = 0

        for item in pending:
            start_ts = item.get('time', '')
            end_ts = ''
            start_s = to_seconds(start_ts) if start_ts else 0
            end_s = start_s + 5.0

            # Build safe filename
            safe_start = start_ts.replace(':', '-').replace(',', '-').replace('.', '-')
            clip_name = f'{self.episode}_{safe_start}.mp4'
            clip_path = os.path.join(out_dir, clip_name)

            # Find adjacent clean cues for context wrapping
            left, right = self._find_adjacent_cues(cues, start_ts)

            clip_start = left['start_s'] if left else max(0, start_s - 3.0)
            clip_end = right['end_s'] if right else end_s + 3.0
            # Clamp to reasonable range
            clip_dur = min(clip_end - clip_start, 30.0)
            clip_end = clip_start + clip_dur

            # Extract clip
            if not os.path.exists(clip_path) and self._video_path:
                ok = self._extract_clip(self._video_path, clip_start,
                                        clip_end, clip_path)
                if ok:
                    extracted += 1
                else:
                    skipped += 1
            elif os.path.exists(clip_path):
                extracted += 1

            entries_md.append(CHECKLIST_ENTRY.format(
                ep=self.episode,
                start=start_ts,
                end=end_ts or '?',
                clip_file=clip_name,
                source='Whisper unfixable',
                original=item.get('original', ''),
            ))

        # Write checklist
        checklist_path = os.path.join(out_dir, f'{self.episode}_checklist.md')
        with open(checklist_path, 'w', encoding='utf-8') as f:
            f.write(CHECKLIST_HEADER.format(
                episode=self.episode, date=today, count=len(entries_md)))
            for entry_md in entries_md:
                f.write(entry_md)

        print(f'[{self.episode}] review: {len(entries_md)} entries → '
              f'{checklist_path} ({extracted} clips, {skipped} skipped)',
              file=sys.stderr)
        return checklist_path

    def apply(self, checklist_path: str) -> int:
        """Apply human corrections from a filled checklist.

        For each ⬜ entry:
        - '修正:' = '删除' → remove cue from SRT → report ✅
        - '修正:' = text → VAD time-align → replace in SRT → report ✅
        - '修正:' = empty → skip, keep ⬜

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

        # Load current SRT
        cues = parse_srt(self._srt_path, mark_garbled=False)
        applied = 0

        for corr in corrections:
            timecode = corr['time']
            text = corr['text']  # may be '删除' or empty or corrected text

            # Find the cue by timecode
            target_idx = None
            for i, cue in enumerate(cues):
                if cue['start'] == timecode:
                    target_idx = i
                    break

            if target_idx is None:
                print(f'[apply] Cue not found: {timecode} — may already be fixed',
                      file=sys.stderr)
                self._mark_checklist_done(corrections, timecode, '✅')
                continue

            if not text or not text.strip():
                # Empty → skip
                continue

            if text.strip() == '删除':
                # Remove cue
                removed = cues.pop(target_idx)
                print(f'[apply] Deleted: {timecode} "{removed["text"][:60]}"',
                      file=sys.stderr)
                self._mark_checklist_done(corrections, timecode, '✅')
                applied += 1
            else:
                # Replace with corrected text (keep original time boundaries)
                cues[target_idx]['text'] = text.strip()
                print(f'[apply] Fixed: {timecode} → "{text.strip()[:60]}"',
                      file=sys.stderr)
                self._mark_checklist_done(corrections, timecode, '✅')
                applied += 1

        # Write back SRT
        if applied > 0:
            write_srt(self._srt_path, cues)
            print(f'[apply] {applied} corrections written to '
                  f'{os.path.basename(self._srt_path)}', file=sys.stderr)

        return applied

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
