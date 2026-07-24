#!/usr/bin/env python3
"""Fix orchestrator — composes SubtitleSession, WhisperFixer, FragmentProcessor.

Three fix sources, cascading priority:
  1. Reference — translated reference SRT comparison (most reliable)
  2. Whisper   — audio transcription with context wrapping
  3. Human     — checklist-based manual correction (last resort)

The orchestrator is the ONLY place that writes SRT and the problem report.
Modules return structured data; the orchestrator applies it.

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

import lib._path  # noqa: F401

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)

from lib.whisper_utils import (
    setup_windows_utf8, to_seconds, format_tc,
    parse_srt, write_srt, apply_fixes_to_srt,
    meaningful_char_count,
)
from utils.update_report import upsert_entries, update_entry_status
setup_windows_utf8()

from fix.subtitle_session import SubtitleSession
from fix.whisper_fixer import WhisperFixer, WhisperResult
from fix.fragment_processor import FragmentProcessor


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
# Fixer — thin orchestrator
# ═══════════════════════════════════════════════════════════════

class Fixer:
    """Fix garbled or mismatched subtitles for one episode.

    Thin orchestrator — composes SubtitleSession, WhisperFixer,
    FragmentProcessor.  Three correction sources, cascading priority:
      Reference → Whisper → Human

    All corrections write to SRT and update the report through
    the same path.  classify_garbled_text guarantees idempotency.
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
        # Compose shared session
        self._session = SubtitleSession(
            episode, project_dir,
            target_lang=target_lang, video_dir=video_dir,
            whisper_cli=whisper_cli, model=model,
            retry_model=retry_model, srt_dir=srt_dir,
        )

        # Compose worker modules (share the same session)
        self._whisper_fixer = WhisperFixer(self._session)
        self._fragment_processor = FragmentProcessor(self._session)

        # ── Backward-compat aliases for external callers ──
        # episode_workflow.py accesses fixer._video_path, _srt_path, etc.
        self.episode = self._session.episode
        self.project_dir = self._session.project_dir
        self.target_lang = self._session.target_lang
        self._srt_dir = self._session.srt_dir
        self._temp_dir = self._session.temp_dir
        self._report_path = self._session.report_path
        self._review_dir = self._session.review_dir
        self._video_dir = self._session.video_dir
        self._video_path = self._session.video_path
        self._srt_path = self._session.srt_path
        self._srt_name = self._session.srt_name
        self._episode_title = self._session.episode_title
        self._ref_srt_path = self._session.ref_srt_path
        self._ref_cues = None
        self._cues = None

        # Whisper config aliases
        self._whisper_cli = self._session.whisper_cli
        self._model = self._session.model
        self._retry_model = self._session.retry_model

    # ── Detection (delegated) ──

    def is_clean(self) -> bool:
        """Check if SRT has any garbled cues remaining."""
        return self._session.is_clean()

    def problem_count(self) -> int:
        """Count garbled cues in current SRT."""
        return self._session.problem_count()

    def _load_cues(self):
        """Load current SRT cues into memory."""
        return self._session.load_cues()

    def _has_missing_subtitles(self) -> bool:
        """Backward compat for episode_workflow.py."""
        return self._session.has_missing_subtitles()

    # ═══════════════════════════════════════════════════════════
    # Source 1: Reference comparison
    # ═══════════════════════════════════════════════════════════

    def fix_by_reference(self, reference_srt: str, *,
                         threshold: float = 0.4) -> FixReport:
        """Fix mismatches by comparing with a translated reference SRT.

        Parses the reference, compares each cue by time overlap with
        the current SRT, and adopts reference text for mismatched cues.
        """
        if not os.path.exists(reference_srt):
            return FixReport(source='reference', failed=0,
                             details=[f'Reference SRT not found: '
                                      f'{reference_srt}'])

        if not self._srt_path:
            return FixReport(source='reference', failed=0,
                             details=['No current SRT to compare'])

        from fix.compare_srt import compare as compare_cues
        from lib.whisper_utils import parse_srt as parse_ref

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

        if fixes:
            apply_fixes_to_srt(self._srt_path, fixes)
            report.applied = len(fixes)

        if unfixable:
            for item in unfixable:
                ts = item.get('time', '')
                for cue in current_cues:
                    if cue.get('start', '') == ts:
                        cue['text'] = '[???]'
                        break
            write_srt(self._srt_path, current_cues)

        report.failed = len(unfixable)
        report.details = [f'{d["verdict"]}: {d.get("whisper","")[:60]}'
                          for d in diffs if d['verdict'] != 'match']

        print(f'[reference] {report.applied} fixed, '
              f'{report.failed} suspicious', file=sys.stderr)
        return report

    # ═══════════════════════════════════════════════════════════
    # Source 2: Whisper (delegates to WhisperFixer)
    # ═══════════════════════════════════════════════════════════

    def fix_by_whisper(self, *, separate_vocals: bool = True,
                       force_tier2: bool = False,
                       skip_vad_clean: bool = False,
                       detect_missing_dialogue: bool = True,
                       missing_dialogue_min_gap: float = 3.0) -> FixReport:
        """Run Whisper auto-fix pipeline.

        Delegates to WhisperFixer, then writes SRT + report from
        the returned WhisperResult.

        Returns FixReport.
        """
        result = self._whisper_fixer.fix_by_whisper(
            separate_vocals=separate_vocals,
            force_tier2=force_tier2,
            skip_vad_clean=skip_vad_clean,
            detect_missing_dialogue=detect_missing_dialogue,
            missing_dialogue_min_gap=missing_dialogue_min_gap,
        )
        return self._apply_whisper_result(result)

    def fix_missing_subtitles(self, gaps=None) -> FixReport:
        """Fill missing subtitles detected by VAD scan.

        Delegates to WhisperFixer, then writes SRT + report.
        """
        result = self._whisper_fixer.fix_missing_subtitles(gaps)
        return self._apply_whisper_result(result)

    def _apply_whisper_result(self, result: WhisperResult) -> FixReport:
        """Write SRT + report from WhisperResult data.

        This is the SINGLE place where Whisper pipeline results
        hit SRT and the problem report.
        """
        # ── Apply to SRT ──
        cues = parse_srt(self._srt_path, mark_garbled=True,
                         target_lang=self.target_lang)

        # Auto-cuts: delete noise cues
        if result.auto_cuts:
            cut_starts = {f['start'] for f in result.auto_cuts}
            cues = [c for c in cues if c.get('start') not in cut_starts]

        # Missing-sub new cues: insert into cue list
        if result.new_cues:
            cues.extend(result.new_cues)
            cues.sort(key=lambda c: c.get('start_s', c.get('start', '')))

        # Write SRT
        if result.auto_cuts or result.new_cues:
            write_srt(self._srt_path, cues)

        # Auto-keep: apply replacement text
        if result.auto_keep_fixes:
            keep_fixes = [f for f in result.auto_keep_fixes
                         if f.get('replacement')]
            if keep_fixes:
                apply_fixes_to_srt(self._srt_path, keep_fixes)

        # AI fragments: delegate to FragmentProcessor
        if result.ai_fragments:
            self._fragment_processor.build_ai_fragments_json(
                result.ai_fragments)

        # Safety: convert remaining placeholders
        if result.placeholder_count > 0:
            self._session.convert_placeholders_to_markers()

        # ── Write report ──
        report = FixReport(source=result.source, tier=result.tier,
                           deleted=result.deleted)
        report.applied = result.applied
        report.ai_review = result.ai_review
        report.details = result.details

        try:
            if result.auto_keep_fixes:
                upsert_entries(self._report_path, step='2', entries=[
                    {'ep': self.episode, 'time': f.get('start', ''),
                     'original': f.get('original', '')[:120],
                     'corrected': (f.get('replacement') or '')[:120],
                     'status': '✅'}
                    for f in result.auto_keep_fixes
                ])
            if result.auto_cuts:
                upsert_entries(self._report_path, step='2', entries=[
                    {'ep': self.episode, 'time': f.get('start', ''),
                     'original': f.get('original', '')[:120],
                     'corrected': '(VAD已删除)', 'status': '🗑️'}
                    for f in result.auto_cuts
                ])
            # Missing-sub entries
            if result.source == 'missing_sub':
                ORIGINAL_MARKER = '(VAD检测到人声但无字幕)'
                if result.auto_keep_fixes:
                    upsert_entries(self._report_path, step='2', entries=[
                        {'ep': self.episode, 'time': f['start'],
                         'original': ORIGINAL_MARKER,
                         'corrected': f.get('replacement', '')[:120],
                         'status': '✅'}
                        for f in result.auto_keep_fixes
                    ])
                if result.ai_fragments:
                    upsert_entries(self._report_path, step='2.5', entries=[
                        {'ep': self.episode, 'time': f['start'],
                         'original': ORIGINAL_MARKER,
                         'corrected': (f.get('replacement') or '')[:120],
                         'status': '⬜'}
                        for f in result.ai_fragments
                    ])
            elif result.ai_fragments:
                upsert_entries(self._report_path, step='2.5', entries=[
                    {'ep': self.episode, 'time': f.get('start', ''),
                     'original': f.get('original', '')[:120],
                     'corrected': (f.get('replacement') or '')[:120],
                     'status': '⬜'}
                    for f in result.ai_fragments
                ])
        except Exception as e:
            import traceback
            print(f'[whisper] Report write failed: {e}', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        return report

    # ═══════════════════════════════════════════════════════════
    # Cascading auto-fix
    # ═══════════════════════════════════════════════════════════

    def run_auto(self) -> FixReport:
        """Run cascading auto-fix: whisper → missing-sub fill → AI context.

        Priority:
        1. If reference SRT exists in 参考字幕/ → inject as AI context
        2. Whisper ASR → triage (auto-keep / AI fragments / auto-cut)
        3. Missing subtitle fill (VAD gaps → Whisper → new cues)
        4. AI fragments include reference_text for Claude to translate+correct
        5. Unfixable items → [???] markers in SRT (review in Aegisub)

        Returns combined FixReport.
        """
        has_missing_subs = self._session.has_missing_subtitles()

        if self._session.is_clean() and not has_missing_subs:
            print(f'[{self.episode}] ✓ clean — nothing to fix',
                  file=sys.stderr)
            return FixReport(source='auto')

        report = FixReport(source='auto')

        # Find reference SRT for AI context injection
        ref_dir = os.path.join(self.project_dir, '参考字幕')
        if os.path.isdir(ref_dir):
            ep_num = self.episode[2:]
            for fname in os.listdir(ref_dir):
                if fname.endswith('.srt') and ep_num in fname:
                    self._ref_srt_path = os.path.join(ref_dir, fname)
                    self._session.ref_srt_path = self._ref_srt_path
                    print(f'[{self.episode}] Reference SRT found → '
                          f'will inject as AI context (no translation)',
                          file=sys.stderr)
                    break

        # Whisper for garbled cues
        if not self._session.is_clean() and self._video_path:
            whisper_result = self.fix_by_whisper()
            report.merge(whisper_result)

        # Missing subtitle fill
        if has_missing_subs and self._video_path:
            miss_result = self.fix_missing_subtitles()
            report.merge(miss_result)

        print(f'\n[{self.episode}] Auto-fix complete: '
              f'{report.applied} applied, '
              f'{report.failed} → manual review',
              file=sys.stderr)
        return report

    # ═══════════════════════════════════════════════════════════
    # Source 3: Human review (legacy)
    # ═══════════════════════════════════════════════════════════

    def review_from_items(self, items: list,
                          output_dir: str = None) -> str | None:
        """Generate manual review checklist + video clips from explicit
        item list.

        Each item dict has keys: 'ep', 'time', 'original'.

        Returns path to checklist.md, or None if no items.
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

        cues = self._load_cues() or parse_srt(
            self._srt_path, mark_garbled=True,
            target_lang=self.target_lang)
        clusters = self._build_review_clusters(cues, pending)

        if not clusters:
            print(f'[{self.episode}] review_from_items: '
                  f'could not build clusters', file=sys.stderr)
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
            safe_start = start_ts.replace(':', '-').replace(',', '-')

            clip_name = f'{safe_start}.mp4'
            clip_path = os.path.join(out_dir, clip_name)

            garbled_start = first_g['start_s']
            garbled_end = last_g['end_s']

            bounds = self._compute_review_clip_bounds(
                garbled_start, garbled_end, cl.get('ss'), cl.get('es'))

            left_text = cl.get('left_text', '')
            right_text = cl.get('right_text', '')
            garbled_texts = ' | '.join(
                g['text'][:60] for g in cl['garbled'])

            if bounds is not None:
                clip_start, clip_end = bounds
                if not os.path.exists(clip_path):
                    if self._video_path:
                        ok = self._extract_clip(
                            self._video_path, clip_start,
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
            checklist_path = os.path.join(
                out_dir, f'{self.episode}_checklist.md')
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

        self._session.save_review_clusters(clusters)

        print(f'[{self.episode}] review_from_items: {len(entries_md)} '
              f'entries → {checklist_path} '
              f'({extracted} clips, {skipped} skipped)',
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
                   if e.get('status') == '⬜'
                   and e.get('ep') == self.episode]

        if not pending:
            print(f'[{self.episode}] review: nothing pending',
                  file=sys.stderr)
            return None

        return self.review_from_items(pending, output_dir)

    def apply(self, checklist_path: str) -> int:
        """Apply corrections from a filled checklist.

        For each entry:
        - '修正:' = '删除' → remove cue from SRT
        - '修正:' = text → VAD time-align → replace in SRT
        - '修正:' = empty → skip

        Returns count of applied corrections.
        """
        if not os.path.exists(checklist_path):
            print(f'[apply] Checklist not found: {checklist_path}',
                  file=sys.stderr)
            return 0

        if not self._srt_path or not os.path.exists(self._srt_path):
            print(f'[apply] SRT not found for {self.episode}',
                  file=sys.stderr)
            return 0

        corrections = self._parse_checklist(checklist_path)
        if not corrections:
            print(f'[apply] No corrections found in checklist',
                  file=sys.stderr)
            return 0

        cues = parse_srt(self._srt_path, mark_garbled=False)
        speech_segs = self._session.load_speech_segs()
        review_clusters = self._session.load_review_clusters()
        applied = 0

        for corr in corrections:
            timecode = corr['time']
            text = corr['text']

            if not text or not text.strip():
                continue

            if text.strip() == '删除':
                target_idx = SubtitleSession.find_cue_index(cues, timecode)
                if target_idx is None:
                    print(f'[apply] Cue not found for delete: {timecode}',
                          file=sys.stderr)
                    continue
                removed = cues.pop(target_idx)
                print(f'[apply] Deleted: {timecode} '
                      f'"{removed["text"][:60]}"', file=sys.stderr)
                applied += 1
                continue

            target_idx = SubtitleSession.find_cue_index(cues, timecode)
            if target_idx is None:
                print(f'[apply] Cue not found: {timecode} — '
                      f'may already be fixed', file=sys.stderr)
                continue

            corrected_text = text.strip()
            cluster = SubtitleSession.find_cluster_for_timecode(
                review_clusters, timecode)

            if cluster and speech_segs and self._video_path:
                aligned = self._fragment_processor._vad_align_correction(
                    cluster, speech_segs, corrected_text,
                    cues[target_idx])
                if aligned:
                    cues[target_idx]['start'] = aligned['start']
                    cues[target_idx]['end'] = aligned['end']
                    cues[target_idx]['text'] = aligned['text']
                    tag = '[VAD]'
                else:
                    cues[target_idx]['text'] = corrected_text
                    tag = '[fallback:orig]'
            else:
                cues[target_idx]['text'] = corrected_text
                tag = '[text-only]'

            print(f'[apply] {tag} {timecode} → "{corrected_text[:60]}"',
                  file=sys.stderr)
            applied += 1

        if applied > 0:
            write_srt(self._srt_path, cues)
            print(f'[apply] {applied} corrections written to '
                  f'{os.path.basename(self._srt_path)}', file=sys.stderr)

        return applied

    # ═══════════════════════════════════════════════════════════
    # AI fragments (delegates to FragmentProcessor)
    # ═══════════════════════════════════════════════════════════

    def apply_ai_fragments(self, json_path: str = None) -> int:
        """Apply AI corrections from temp JSON.

        Delegates to FragmentProcessor.
        Returns count of applied corrections.
        """
        return self._fragment_processor.apply_ai_fragments(json_path)

    def _write_ai_fragments_json(self, fragments: list) -> str | None:
        """Backward compat — delegates to FragmentProcessor."""
        return self._fragment_processor.build_ai_fragments_json(fragments)

    # ═══════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════

    # ── Review-specific clustering (tighter than Whisper's 60s gap) ──
    REVIEW_CLUSTER_GAP = 8.0

    def _build_review_clusters(self, cues, pending_items):
        """Build tight review clusters for human review video clips.

        Uses 8s gap (not 60s) — groups only nearby garbled cues.
        Does NOT expand to adjacent clean cue boundaries for clip
        extraction; _compute_review_clip_bounds uses VAD instead.
        Still captures left/right clean cue TEXT for context display
        but NOT for clip time boundaries.
        """
        pending_starts = {e.get('time', '') for e in pending_items}
        for c in cues:
            if c.get('start', '') in pending_starts:
                c['is_garbled'] = True

        garbled_cues = [c for c in cues if c.get('is_garbled')
                        and c.get('start', '') in pending_starts]
        if not garbled_cues:
            return []

        groups = [[g] for g in garbled_cues]

        clusters = []
        for g_group in groups:
            first, last = g_group[0], g_group[-1]

            first_idx = next((k for k, c in enumerate(cues)
                             if c['start'] == first['start']), 0)
            last_idx = next((k for k, c in enumerate(cues)
                            if c['start'] == last['start']), 0)

            left = next((cues[k] for k in range(first_idx - 1, -1, -1)
                        if not cues[k].get('is_garbled')
                        and cues[k]['text']), None)
            right = next((cues[k] for k in range(last_idx + 1, len(cues))
                         if not cues[k].get('is_garbled')
                         and cues[k]['text']), None)

            REVIEW_SEARCH_WINDOW = 5.0
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

    def _find_adjacent_cues(self, cues: list, timecode: str):
        """Find clean cues immediately before and after a given timecode.

        Returns (left_cue, right_cue), either may be None.
        """
        target_s = to_seconds(timecode) if timecode else 0
        left, right = None, None

        sorted_cues = sorted(cues, key=lambda c: c['start_s'])

        for cue in sorted_cues:
            if cue['end_s'] <= target_s:
                if not cue.get('is_garbled') and cue.get('text'):
                    left = cue
            elif cue['start_s'] >= target_s + 5.0:
                if not cue.get('is_garbled') and cue.get('text'):
                    right = cue
                    break

        return left, right

    def _compute_review_clip_bounds(self, garbled_start, garbled_end,
                                    cluster_ss, cluster_es,
                                    max_pad=2.0):
        """Compute video clip boundaries for human review — VAD-aware v2.

        Delegates to SubtitleSession static method.
        """
        speech_segs = self._session.load_speech_segs()
        return SubtitleSession.compute_review_clip_bounds(
            garbled_start, garbled_end, cluster_ss, cluster_es,
            speech_segs, max_pad)

    def _extract_clip(self, video_path: str, start_s: float,
                      end_s: float, output_path: str) -> bool:
        """Extract a video clip using ffmpeg. Returns True on success."""
        clip_start = max(0, start_s)
        duration = end_s - start_s
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

        Each correction: {'time': '00:02:00.000', 'text': '...'}
        text may be '删除', empty, or the corrected subtitle text.
        """
        if not os.path.exists(path):
            return []

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'version: 2' not in content:
            print('[apply] Old checklist format detected — ignoring.'
                  ' Re-run Fixer.review() to generate a new one.',
                  file=sys.stderr)
            return []

        corrections = []
        blocks = re.split(r'\n---\n', content)

        for block in blocks:
            time_match = re.search(
                r'(\d{2}:\d{2}:\d{2}[.,]\d{3})'
                r'\s*~\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})?',
                block)
            if not time_match:
                continue

            timecode = time_match.group(1).replace(',', '.')

            if re.search(r'^\s*✅', block, re.MULTILINE):
                continue

            corr_match = re.search(
                r'修正:\s*\n?(.+?)(?=\n---|\n\Z|\Z)', block, re.DOTALL)
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
    parser.add_argument('--step',
                        choices=['check', 'whisper', 'review', 'apply'],
                        help='Run a specific step')
    parser.add_argument('--checklist',
                        help='Checklist path for --step apply')
    parser.add_argument('--reference',
                        help='Reference SRT path for --step reference')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    fixer = Fixer(args.episode, args.project_dir,
                  target_lang=args.target_lang)

    if args.step == 'check':
        clean = fixer.is_clean()
        count = fixer.problem_count()
        print(f'{args.episode}: '
              f'{"clean" if clean else f"{count} garbled cues"}')

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
        if args.dry_run:
            clean = fixer.is_clean()
            print(f'{args.episode}: '
                  f'{"clean — nothing to do" if clean else "would run auto-fix"}')
        else:
            fixer.run_auto()
