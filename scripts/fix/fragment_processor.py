#!/usr/bin/env python3
"""AI fragment JSON generation + application + VAD alignment.

Builds per-episode ai_fragments_{EP}.json for AI review, and applies
AI corrections with VAD time-alignment, auto-cut decisions, and [???]
marker escalation for unfilled items.
"""

import json
import os
import sys
from datetime import datetime

import lib._path  # noqa: F401

from lib.whisper_utils import (
    setup_windows_utf8, parse_srt, write_srt,
    to_seconds, format_tc, meaningful_char_count, is_length_anomaly,
)
from utils.update_report import update_entry_status
setup_windows_utf8()

from fix.subtitle_session import SubtitleSession


class FragmentProcessor:
    """AI fragments: JSON generation + apply + VAD alignment.

    NOTE: apply_ai_fragments() writes SRT + report directly — this is a
    pragmatic exception to the data-contract rule because the method is:
      (a) called directly by run_all.py during the --apply-ai-review phase,
      (b) too tightly coupled with VAD alignment + paired-cue mode
          for clean data-contract separation at this stage.
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
    def _temp_dir(self): return self._session.temp_dir

    @property
    def _report_path(self): return self._session.report_path

    @property
    def _target_lang(self): return self._session.target_lang

    @property
    def _episode_title(self): return self._session.episode_title

    # ═══════════════════════════════════════════════════════════
    # AI fragments JSON generation
    # ═══════════════════════════════════════════════════════════

    def build_ai_fragments_json(self, fragments: list) -> str | None:
        """Build and write ai_fragments_{EP}.json for AI review.

        Each fragment dict has keys: 'start', 'end', 'original',
        'replacement', 'whisper_original_ja'.

        Returns path to JSON file, or None if fragments is empty.
        """
        if not fragments:
            return None

        cues = (self._session.load_cues()
                or parse_srt(self._srt_path, mark_garbled=False))

        os.makedirs(self._temp_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        entries = []

        for f in fragments:
            start_ts = f.get('start', '')
            end_ts = f.get('end', '')
            original = f.get('original', '')
            corrected = f.get('replacement', '')

            # Level 1: SRT context (6 cues each side)
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

            # Level 2: Whisper transcript window (±30s)
            whisper_ctx = self._extract_whisper_context(start_s, end_s)

            # Level 3: Reference subtitle context (original language)
            ref_text = self._session.find_ref_text(start_s, end_s)

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
            # Paired mode: hallucination-suspect fragments
            if corrected and is_length_anomaly(original, corrected):
                entry['mode'] = 'paired'
                entry['paired_cues'] = self._build_paired_cues(cues, f)
            entries.append(entry)

        json_path = os.path.join(self._temp_dir,
                                 f'ai_fragments_{self._episode}.json')
        data = {
            'episode': self._episode,
            'episode_title': self._episode_title,
            'exported': today,
            'fragments': entries,
        }
        with open(json_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

        print(f'[{self._episode}] build_ai_fragments_json: {len(entries)} '
              f'entries → {json_path}', file=sys.stderr)
        return json_path

    # ═══════════════════════════════════════════════════════════
    # AI fragments application
    # ═══════════════════════════════════════════════════════════

    def apply_ai_fragments(self, json_path: str = None) -> int:
        """Apply AI-filled corrections from temp JSON to SRT + report.

        Reads ai_fragments_{EP}.json, applies every fragment with a
        non-empty 'correction' field to SRT.  Skipped fragments are
        VAD-checked: noise → auto-cut, speech → [???] marker.

        Returns count of applied corrections.
        """
        if json_path is None:
            json_path = os.path.join(self._temp_dir,
                                     f'ai_fragments_{self._episode}.json')
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
            print(f'[apply-ai] SRT not found for {self._episode}',
                  file=sys.stderr)
            return 0

        cues = parse_srt(self._srt_path, mark_garbled=False)
        speech_segs = self._session.load_speech_segs()
        review_clusters = self._session.load_review_clusters()
        applied = 0
        unfilled = []

        for frag in fragments:
            timecode = frag.get('start', '')
            correction = frag.get('correction', '').strip()

            if not correction:
                unfilled.append({
                    'ep': self._episode, 'time': timecode,
                    'original': frag.get('original', ''),
                    'context_before': frag.get('context_before', []),
                    'context_after': frag.get('context_after', []),
                })
                continue

            if correction == '删除':
                target_idx = SubtitleSession.find_cue_index(cues, timecode)
                if target_idx is None:
                    print(f'[apply-ai] Cue not found for delete: {timecode}',
                          file=sys.stderr)
                    continue
                removed = cues.pop(target_idx)
                print(f'[apply-ai] Deleted: {timecode} '
                      f'"{removed["text"][:60]}"', file=sys.stderr)
                applied += 1
                continue

            target_idx = SubtitleSession.find_cue_index(cues, timecode)
            if target_idx is None:
                print(f'[apply-ai] Cue not found: {timecode} — may be fixed',
                      file=sys.stderr)
                continue

            cluster = SubtitleSession.find_cluster_for_timecode(
                review_clusters, timecode)
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
                    pc_idx = SubtitleSession.find_cue_index(cues, pc_start)
                    if pc_idx is None:
                        continue
                    if pc_corr == '__DELETE__':
                        cues.pop(pc_idx)
                        applied += 1
                    else:
                        cues[pc_idx]['text'] = pc_corr
                        applied += 1

            # Update report
            try:
                ok = update_entry_status(self._report_path, step='2.5',
                                         ep=self._episode, time=timecode,
                                         corrected=correction, status='✅')
                if not ok:
                    print(f'[apply-ai] Report entry not found for '
                          f'{self._episode} {timecode} — may be in another '
                          f'step', file=sys.stderr)
            except Exception as e:
                print(f'[apply-ai] Report update failed: {e}',
                      file=sys.stderr)

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
                original = entry.get('original', '')
                if meaningful_char_count(original, self._target_lang) < 2:
                    cues = [c for c in cues
                            if c.get('start', '') != ts]
                    auto_cut += 1
                    try:
                        update_entry_status(self._report_path, step='2.5',
                                            ep=self._episode, time=ts,
                                            corrected='(原文无语义)',
                                            status='🗑️')
                    except Exception:
                        pass
                    continue
                has_speech = any(
                    es >= start_s and ss <= start_s + 5.0
                    for ss, es in speech_segs
                )
                if has_speech:
                    ctx_before = entry.get('context_before', [])
                    ctx_after = entry.get('context_after', [])
                    ctx_cues = ctx_before + ctx_after
                    if ctx_cues:
                        noise_count = sum(
                            1 for t in ctx_cues
                            if meaningful_char_count(t,
                                                     self._target_lang) < 2
                        )
                        if noise_count / len(ctx_cues) >= 0.6:
                            cues = [c for c in cues
                                    if c.get('start', '') != ts]
                            auto_cut += 1
                            try:
                                update_entry_status(
                                    self._report_path, step='2.5',
                                    ep=self._episode, time=ts,
                                    corrected='(噪音包围)', status='🗑️')
                            except Exception:
                                pass
                            continue
                    escalated.append(entry)
                else:
                    cues = [c for c in cues
                            if c.get('start', '') != ts]
                    auto_cut += 1
                    try:
                        update_entry_status(self._report_path, step='2.5',
                                            ep=self._episode, time=ts,
                                            corrected='(VAD无语音)',
                                            status='🗑️')
                    except Exception:
                        pass
            if auto_cut > 0:
                write_srt(self._srt_path, cues)

        if escalated:
            for entry in escalated:
                ts = entry['time']
                target_idx = SubtitleSession.find_cue_index(cues, ts)
                if target_idx is not None:
                    cues[target_idx]['text'] = '[???]'
            write_srt(self._srt_path, cues)
            try:
                for entry in escalated:
                    update_entry_status(self._report_path, step='2.5',
                                        ep=self._episode,
                                        time=entry['time'],
                                        corrected='[???]', status='⬜')
            except Exception:
                pass
            print(f'[apply-ai] {len(escalated)} → [???] markers '
                  f'(review in Aegisub), {auto_cut} auto-cut',
                  file=sys.stderr)

        # Cleanup: remove the temp JSON
        try:
            os.remove(json_path)
        except OSError:
            pass

        return applied

    # ── Internal helpers ──

    def _extract_whisper_context(self, start_s: float, end_s: float,
                                 window_s: float = 30.0) -> list:
        """Extract Whisper transcript segments within ±window_s of fragment.

        Reads temp/scans/whisper_full_{EP}.json (generated by Tier 2).
        Returns [{start_s: float, text: str}, ...] — compact, sorted.
        """
        transcript_path = os.path.join(
            self._temp_dir, f'whisper_full_{self._episode}.json')
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

        if len(window_segs) > 20:
            mid = (start_s + end_s) / 2
            window_segs.sort(key=lambda s: abs(s['start_s'] - mid))
            window_segs = window_segs[:20]
            window_segs.sort(key=lambda s: s['start_s'])

        return window_segs

    def _build_paired_cues(self, cues: list, target_fragment: dict) -> list:
        """Build paired-cue list for hallucination-suspect fragments.

        Finds the best neighbor cue to pair with the target, allowing AI
        to edit either (or both) to make the combined passage coherent.
        """
        target_start = target_fragment.get('start', '')
        target_start_s = to_seconds(target_start) if target_start else 0
        target_end = target_fragment.get('end', '')
        target_end_s = (to_seconds(target_end) if target_end
                        else target_start_s + 3.0)
        target_text = target_fragment.get(
            'replacement', target_fragment.get('original', ''))

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

    def _vad_align_correction(self, cluster: dict, speech_segs: list,
                              corrected_text: str, cue: dict) -> dict | None:
        """Use VAD to find precise speech boundaries for corrected text.

        Returns dict {start, end, text} if alignment succeeds,
        None if fallback needed.
        """
        cluster_ss = cluster['ss']
        cluster_es = cluster['es']
        overlapping = []
        for ss, es in speech_segs:
            overlap = min(es, cluster_es) - max(ss, cluster_ss)
            if overlap > 0.3:
                overlapping.append((ss, es))

        n = len(overlapping)
        n_garbled = len(cluster.get('garbled', []))

        if n == 0:
            print(f'[VAD] 0 speech segments in cluster → likely non-speech, '
                  f'keeping original boundaries', file=sys.stderr)
            return None

        elif n == 1:
            ss, es = overlapping[0]
            return {
                'start': format_tc(ss),
                'end': format_tc(es),
                'text': corrected_text,
            }

        elif n == n_garbled:
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
