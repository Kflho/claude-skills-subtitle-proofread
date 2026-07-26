#!/usr/bin/env python3
"""OP/ED blank-cue filler — 3-step API-driven pipeline.

Fills empty cues in OP/ED regions by extracting audio, classifying
instrumental vs vocal via LLM, translating lyrics, and applying
a unified template across all episodes.

Three steps (all decisions via API, no heuristic rules):

  Step 1 — API boundary detection
    Sample 5 episodes' cue lists → LLM detects OP/ED/preview boundaries.
    Replaces hardcoded 95s/120s with semantic understanding of where
    blank/dense-music regions end and continuous dialogue begins.

  Step 2 — API instrumental vs vocal classification
    Extract OP/ED audio → Whisper transcribe → LLM judges whether
    Whisper output is random hallucination (instrumental) or
    semantically consistent fragments (vocal with lyrics).
    Core signal: cross-episode semantic similarity.

  Step 3 — API translation + version detection + template fill
    If vocal: LLM detects song versions, translates ja→zh,
    outputs standardized lyric template. Template is applied to
    all episodes' OP/ED regions.
    Safety: only fills blank cues, only within conservative bounds.

Usage:
  # Full pipeline with API boundary detection
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh

  # Dry-run (preview without modifying files)
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh --dry-run

  # Skip boundary detection (use defaults)
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh --skip-step1
"""

import json
import os
import re
import sys
import tempfile
import urllib.request
from collections import defaultdict

import lib._path  # noqa: F401
from lib.config import (LLM_API_KEY, LLM_MODEL, LLM_BASE_URL,
                        LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT,
                        WHISPER_CLI, WHISPER_MODEL)
from lib.subtitle_io import read_subtitles, write_subtitles
from lib.whisper_utils import (OP_BOUNDARY_SEC, ED_BOUNDARY_SEC,
                               extract_audio_wav, run_whisper)
from lib.project_utils import find_video
from lib.oped_detect import detect_boundaries as api_detect_boundaries


# ── Constants ────────────────────────────────────────────────────

SAMPLE_COUNT = 5          # episodes to sample for boundary detection
WHISPER_SAMPLE_COUNT = 3   # episodes to Whisper for vocal/instrumental check


# ── LLM helpers ──────────────────────────────────────────────────

def _call_llm(messages, api_key, model, base_url,
              temperature=0.1, max_tokens=4096):
    """Call OpenAI-compatible chat API."""
    url = f'{base_url}/chat/completions'
    body = {
        'model': model, 'messages': messages,
        'temperature': temperature, 'max_tokens': max_tokens,
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except Exception as e:
        print(f'  [oped-fill] API error: {e}', file=sys.stderr)
        return None


def _parse_json_response(response):
    """Parse JSON from LLM response, handling markdown code blocks."""
    if not response:
        return None
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[\w-]*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    for s in [cleaned]:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass

    m = re.search(r'[\[{].*[\]}]', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    print('  [oped-fill] Could not parse JSON from LLM response', file=sys.stderr)
    return None


# ═══════════════════════════════════════════════════════════════
# OpedFill
# ═══════════════════════════════════════════════════════════════

class OpedFill:
    """Fill blank cues in OP/ED regions using API-driven pipeline."""

    def __init__(self, srt_dir, *,
                 video_dir=None,
                 lang='auto',
                 api_key=None,
                 model=None,
                 base_url=None,
                 op_boundary=OP_BOUNDARY_SEC,
                 ed_boundary=ED_BOUNDARY_SEC,
                 skip_step1=False,
                 dry_run=False):
        self.srt_dir = srt_dir
        self.video_dir = video_dir
        self.lang = lang
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL or LLM_MODEL_DEFAULT
        self.base_url = base_url or LLM_BASE_URL or LLM_BASE_URL_DEFAULT
        self.op_boundary = op_boundary
        self.ed_boundary = ed_boundary
        self.skip_step1 = skip_step1
        self.dry_run = dry_run

        self.boundaries = {}
        self.classification = {}   # {region: 'instrumental'|'vocal'|'uncertain'}
        self.lyric_templates = []  # [{song_id, episodes, lyrics: [{ja, zh, time_s}]}]
        self.fixes = []            # [{action, file, start, original, replacement, note}]

    # ── Step 1: Boundary detection ───────────────────────────────

    def step1_detect_boundaries(self):
        """Detect OP/ED/preview boundaries via API (LLM analysis of cue patterns)."""
        print('\n[oped-fill] === Step 1: API boundary detection ===', file=sys.stderr)

        if self.skip_step1:
            print('[oped-fill] Skipped (--skip-step1). Using defaults: '
                  f'OP={self.op_boundary}s, ED={self.ed_boundary}s', file=sys.stderr)
            self.boundaries = {
                'op_start_s': 0.0, 'op_end_s': float(self.op_boundary),
                'ed_start_s': None, 'ed_end_s': float(self.ed_boundary),
                'preview_end_s': None,
                'confidence': 'fallback', 'per_episode_notes': {}, 'warnings': [],
            }
            return self.boundaries

        result = api_detect_boundaries(
            self.srt_dir,
            sample_count=SAMPLE_COUNT,
            lang=self.lang,
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            dry_run=self.dry_run,
        )

        self.boundaries = result
        print(f'[oped-fill] Boundaries: OP=0-{result["op_end_s"]:.0f}s, '
              f'ED={result.get("ed_end_s", "N/A")}, '
              f'confidence={result["confidence"]}', file=sys.stderr)
        for w in result.get('warnings', []):
            print(f'[oped-fill] ⚠ {w}', file=sys.stderr)
        return result

    # ── Step 2: Instrumental vs Vocal classification ─────────────

    def step2_classify(self):
        """Classify OP/ED as instrumental or vocal via API.

        Extracts OP/ED audio from sample episodes, runs Whisper,
        sends cross-episode Whisper output to LLM for classification.
        """
        print('\n[oped-fill] === Step 2: API classification ===', file=sys.stderr)

        if self.dry_run:
            print('[oped-fill] DRY RUN — skipping audio extraction', file=sys.stderr)
            self.classification = {'OP': 'uncertain', 'ED': 'uncertain'}
            return self.classification

        if not self.video_dir:
            print('[oped-fill] No --video-dir, assuming vocal (will attempt fill).',
                  file=sys.stderr)
            self.classification = {'OP': 'vocal', 'ED': 'vocal'}
            return self.classification

        sub_files = sorted([
            f for f in os.listdir(self.srt_dir)
            if f.lower().endswith(('.srt', '.ass'))
        ])
        if not sub_files:
            self.classification = {}
            return self.classification

        # Sample episodes
        if len(sub_files) <= WHISPER_SAMPLE_COUNT:
            sampled = sub_files
        else:
            step = len(sub_files) / WHISPER_SAMPLE_COUNT
            sampled = [sub_files[int(i * step)] for i in range(WHISPER_SAMPLE_COUNT)]

        op_end = self.boundaries.get('op_end_s', self.op_boundary)
        ed_boundary_s = self.boundaries.get('ed_end_s', self.ed_boundary)

        all_outputs = {'OP': [], 'ED': []}

        with tempfile.TemporaryDirectory(prefix='oped_fill_') as tmpdir:
            for fname in sampled:
                ep_match = re.search(r'(\d+)', fname)
                ep_num = ep_match.group(1) if ep_match else fname

                video_path = find_video(
                    os.path.dirname(self.srt_dir), ep_num,
                    video_dir=self.video_dir,
                )
                if not video_path or not os.path.exists(video_path):
                    print(f'  [oped-fill] No video for {fname}, skipping', file=sys.stderr)
                    continue

                # Extract OP audio
                op_wav = os.path.join(tmpdir, f'{ep_num}_op.wav')
                extract_audio_wav(video_path, op_wav, ss=0, duration=op_end)

                # Extract ED audio
                ed_wav = os.path.join(tmpdir, f'{ep_num}_ed.wav')
                extract_audio_wav(video_path, ed_wav, ss=-ed_boundary_s)

                for region, wav in [('OP', op_wav), ('ED', ed_wav)]:
                    if not os.path.exists(wav):
                        continue
                    segs = run_whisper(
                        wav, WHISPER_CLI, WHISPER_MODEL,
                        language='ja',
                        threads=8, processors=2,
                        beam_size=5, best_of=8,
                    )
                    if segs:
                        for seg in segs:
                            text = seg.get('text', '').strip()
                            if text:
                                all_outputs[region].append({
                                    'ep': fname, 'region': region,
                                    'text': text,
                                    'start_s': seg.get('start_s', 0),
                                })

        # Send to LLM for classification
        classification = {}
        for region in ['OP', 'ED']:
            outputs = all_outputs[region]
            if not outputs:
                print(f'  [oped-fill] {region}: No Whisper output → uncertain',
                      file=sys.stderr)
                classification[region] = 'uncertain'
                continue

            print(f'  [oped-fill] {region}: {len(outputs)} segments → API classify',
                  file=sys.stderr)
            result = self._api_classify(region, outputs)
            classification[region] = result
            print(f'  [oped-fill] {region} → {result}', file=sys.stderr)

        self.classification = classification
        return classification

    def _api_classify(self, region, whisper_outputs):
        """LLM classifies instrumental vs vocal from cross-episode Whisper output."""
        by_episode = defaultdict(list)
        for item in whisper_outputs:
            by_episode[item['ep']].append(
                f"[{item['start_s']:.1f}s] {item['text']}"
            )

        ep_sections = []
        for ep, lines in sorted(by_episode.items()):
            ep_sections.append(f"=== {ep} ===\n" + '\n'.join(lines))

        prompt = (
            f"你是音频分析助手。以下是对同一动画的 {region}（片头/片尾曲）区间"
            f"用日语 Whisper 模型转写的结果，来自 {len(by_episode)} 集。\n\n"
            f"请判断这段音频是：\n"
            f"A) 纯器乐（无歌词人声）— Whisper 产生随机幻觉，跨集输出不一致\n"
            f"B) 人声歌曲（有歌词）— Whisper 输出碎片，跨集有语义相似性\n"
            f"C) 不确定\n\n"
            f"判断标准：\n"
            f"- 多集输出高度相似（同样的假名/汉字片段反复出现）→ B（人声）\n"
            f"- 每集输出完全不同，且看起来像随机假名碎片 → A（器乐）\n"
            f"- 拉丁字母通常是幻觉信号\n\n"
            f"各集 Whisper 输出：\n\n"
            f"{chr(10).join(ep_sections)}\n\n"
            f"请只回复一个字母（A/B/C）和简短理由。"
        )

        response = _call_llm(
            [{'role': 'user', 'content': prompt}],
            self.api_key, self.model, self.base_url,
            temperature=0.0,
        )
        if not response:
            return 'uncertain'

        response = response.strip().upper()
        if response.startswith('A'):
            return 'instrumental'
        elif response.startswith('B'):
            return 'vocal'
        return 'uncertain'

    # ── Step 3: Translation + template fill ──────────────────────

    def step3_fill(self):
        """Translate lyrics via API and apply template to blank cues."""
        print('\n[oped-fill] === Step 3: Translation + template fill ===',
              file=sys.stderr)

        if self.dry_run:
            print('[oped-fill] DRY RUN — would fill blank cues', file=sys.stderr)
            return []

        vocal_regions = [r for r, c in self.classification.items()
                         if c == 'vocal']
        if not vocal_regions:
            print('[oped-fill] No vocal OP/ED detected — nothing to fill.',
                  file=sys.stderr)
            return []

        op_end = self.boundaries.get('op_end_s', self.op_boundary)
        ed_boundary_s = self.boundaries.get('ed_end_s', self.ed_boundary)

        sub_files = sorted([
            f for f in os.listdir(self.srt_dir)
            if f.lower().endswith(('.srt', '.ass'))
        ])
        whisper_sample = min(len(sub_files), 10)

        all_lyrics = {}

        with tempfile.TemporaryDirectory(prefix='oped_fill_lyrics_') as tmpdir:
            for fname in sub_files[:whisper_sample]:
                ep_match = re.search(r'(\d+)', fname)
                ep_num = ep_match.group(1) if ep_match else fname

                video_path = find_video(
                    os.path.dirname(self.srt_dir), ep_num,
                    video_dir=self.video_dir,
                )
                if not video_path or not os.path.exists(video_path):
                    continue

                for region in vocal_regions:
                    if region == 'OP':
                        wav = os.path.join(tmpdir, f'{ep_num}_op_lyrics.wav')
                        extract_audio_wav(video_path, wav, ss=0, duration=op_end)
                    else:
                        wav = os.path.join(tmpdir, f'{ep_num}_ed_lyrics.wav')
                        extract_audio_wav(video_path, wav, ss=-ed_boundary_s)

                    if not os.path.exists(wav):
                        continue

                    segs = run_whisper(
                        wav, WHISPER_CLI, WHISPER_MODEL,
                        language='ja',
                        threads=8, processors=2,
                        beam_size=5, best_of=8,
                    )
                    if segs:
                        if region not in all_lyrics:
                            all_lyrics[region] = []
                        for seg in segs:
                            text = seg.get('text', '').strip()
                            if text:
                                all_lyrics[region].append({
                                    'ep': fname, 'text': text,
                                    'start_s': seg.get('start_s', 0),
                                    'end_s': seg.get('end_s', 0),
                                })

        # Get lyric templates from LLM per region
        for region in vocal_regions:
            outputs = all_lyrics.get(region, [])
            if not outputs:
                print(f'  [oped-fill] {region}: No Whisper output', file=sys.stderr)
                continue

            print(f'  [oped-fill] {region}: {len(outputs)} segments → '
                  f'API translate', file=sys.stderr)
            templates = self._api_translate(region, outputs)
            if templates:
                self.lyric_templates.extend(templates)
                for t in templates:
                    print(f'    "{t["song_id"]}": {len(t["lyrics"])} lines, '
                          f'episodes {t.get("episodes", "all")}',
                          file=sys.stderr)

        # Apply templates
        if self.lyric_templates:
            self._apply_templates(sub_files)
        else:
            print('[oped-fill] No templates generated.', file=sys.stderr)

        return self.fixes

    def _api_translate(self, region, whisper_outputs):
        """LLM translates lyrics and detects song versions."""
        by_ep = defaultdict(list)
        for item in whisper_outputs:
            by_ep[item['ep']].append(
                f"[{item['start_s']:.1f}-{item['end_s']:.1f}s] {item['text']}"
            )

        ep_lines = []
        for ep in sorted(by_ep.keys())[:8]:
            ep_lines.append(f"=== {ep} ===\n" + '\n'.join(by_ep[ep][:15]))

        prompt = (
            f"你是日语歌词翻译助手。以下是同一动画的 {region}（片头/片尾曲）"
            f"用 Whisper 转写的日语歌词片段，来自 {len(by_ep)} 集。\n\n"
            f"请完成任务：\n"
            f"1. 判断是否存在多首不同的歌曲（如 OP1 换成 OP2）\n"
            f"2. 对每首歌，根据碎片重建完整的日语歌词和中文翻译\n"
            f"3. 标注每句歌词的出现时间（秒，相对于 {region} 开始）\n\n"
            f"注意：Whisper 输出有噪声/幻觉，需要你判断真实歌词。\n"
            f"翻译要符合中文歌词的韵律。\n\n"
            f"各集 Whisper 输出：\n\n"
            f"{chr(10).join(ep_lines)}\n\n"
            f"请以 JSON 格式输出：\n"
            f'[\n  {{\n'
            f'    "song_id": "OP1",\n'
            f'    "episodes": "001-052",\n'
            f'    "lyrics": [\n'
            f'      {{"ja": "...", "zh": "...", "time_s": 5.0}}\n'
            f'    ]\n'
            f'  }}\n'
            f']\n\n'
            f'如果检测不到连贯歌词，返回 []。'
        )

        response = _call_llm(
            [{'role': 'user', 'content': prompt}],
            self.api_key, self.model, self.base_url,
            temperature=0.2, max_tokens=8192,
        )
        result = _parse_json_response(response)
        if isinstance(result, list) and result:
            return result
        return []

    def _apply_templates(self, sub_files):
        """Apply lyric templates to blank cues in OP/ED regions.

        Safety: only fills blank cues, only within boundary, time-matched.
        """
        op_end = self.boundaries.get('op_end_s', self.op_boundary)
        ed_boundary_s = self.boundaries.get('ed_end_s', self.ed_boundary)

        fixes = []

        for fname in sub_files:
            fpath = os.path.join(self.srt_dir, fname)
            cues = list(read_subtitles(fpath, mark_garbled=False))
            if not cues:
                continue

            max_end = max(c['end_s'] for c in cues)
            ed_start = max(0, max_end - ed_boundary_s)

            ep_match = re.search(r'(\d+)', fname)
            ep_num = int(ep_match.group(1)) if ep_match else 0

            modified = False
            for i, cue in enumerate(cues):
                text = cue['text'].strip()
                region = None

                if cue['start_s'] < op_end:
                    region = 'OP'
                elif cue['start_s'] >= ed_start:
                    region = 'ED'

                if not region or text:
                    # Not in OP/ED, or already has text → skip
                    continue

                # Match template by time
                matched = self._match_lyric(region, cue['start_s'], ep_num)
                if matched:
                    cues[i]['text'] = matched['zh']
                    fixes.append({
                        'action': 'fill_blank',
                        'file': fname,
                        'start': cue['start'],
                        'original': '',
                        'replacement': matched['zh'],
                        'note': (f'{region} 歌词填充: '
                                 f'{matched.get("ja", "")} → {matched["zh"]}'),
                    })
                    modified = True

            if modified and not self.dry_run:
                write_subtitles(cues, fpath)

        self.fixes = fixes
        n_eps = len(set(f['file'] for f in fixes))
        print(f'[oped-fill] {len(fixes)} blank cues filled across {n_eps} episodes',
              file=sys.stderr)

    def _match_lyric(self, region, start_s, ep_num):
        """Match a time position to a lyric template line.

        Finds the correct song version for this episode, then matches
        by closest time position (±3 second tolerance).
        """
        for template in self.lyric_templates:
            song_id = template.get('song_id', '')
            if not song_id.startswith(region):
                continue

            ep_range = template.get('episodes', 'all')
            if ep_range != 'all':
                try:
                    parts = ep_range.split('-')
                    lo, hi = int(parts[0]), int(parts[-1])
                    if not (lo <= ep_num <= hi):
                        continue
                except (ValueError, IndexError):
                    pass

            for lyric in template.get('lyrics', []):
                lyric_time = lyric.get('time_s', 0)
                if abs(start_s - lyric_time) <= 3.0:
                    return lyric
        return None

    # ── Run ──────────────────────────────────────────────────────

    def run(self):
        """Run the full 3-step pipeline."""
        print('╔══════════════════════════════════════╗', file=sys.stderr)
        print('║  oped_fill — OP/ED blank-cue filler  ║', file=sys.stderr)
        print('╚══════════════════════════════════════╝', file=sys.stderr)
        print(f'  SRT: {self.srt_dir}', file=sys.stderr)
        print(f'  Video: {self.video_dir or "N/A"}', file=sys.stderr)
        print(f'  Lang: {self.lang}  Dry-run: {self.dry_run}', file=sys.stderr)

        self.step1_detect_boundaries()
        self.step2_classify()
        self.step3_fill()

        summary = {
            'boundaries': self.boundaries,
            'classification': self.classification,
            'templates': len(self.lyric_templates),
            'total_fixes': len(self.fixes),
            'fixes': self.fixes,
        }

        print(f'\n=== OP/ED Fill Report ===', file=sys.stderr)
        print(f'  Boundaries:     OP 0-{self.boundaries.get("op_end_s", "?")}s, '
              f'ED last {self.boundaries.get("ed_end_s", "?")}s '
              f'({self.boundaries.get("confidence", "?")})',
              file=sys.stderr)
        print(f'  Classification: {self.classification}', file=sys.stderr)
        print(f'  Song versions:  {len(self.lyric_templates)}', file=sys.stderr)
        print(f'  Blank filled:   {len(self.fixes)}', file=sys.stderr)

        return summary


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    import argparse
    parser = argparse.ArgumentParser(
        description='OP/ED blank-cue filler — 3-step API-driven pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline with API boundary detection
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh

  # Dry-run preview (no file changes)
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh --dry-run

  # Skip boundary detection, use defaults
  python oped_fill.py 打轴版/ --video-dir "D:/Video/..." --lang zh --skip-step1
        """
    )
    parser.add_argument('srt_dir', help='Directory containing SRT/ASS files')
    parser.add_argument('--video-dir',
                        help='Directory containing video files '
                        '(required for audio extraction in Step 2/3)')
    parser.add_argument('--lang', default='auto',
                        help='Target language: auto, ja, zh. Default: auto.')
    parser.add_argument('--op-boundary', type=float, default=OP_BOUNDARY_SEC,
                        help=f'Fallback OP boundary in seconds (default: {OP_BOUNDARY_SEC})')
    parser.add_argument('--ed-boundary', type=float, default=ED_BOUNDARY_SEC,
                        help=f'Fallback ED boundary in seconds (default: {ED_BOUNDARY_SEC})')
    parser.add_argument('--skip-step1', action='store_true',
                        help='Skip API boundary detection, use --op-boundary/--ed-boundary')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without modifying files')
    parser.add_argument('--output', '-o', help='Output JSON path for fill report')
    args = parser.parse_args()

    if not os.path.isdir(args.srt_dir):
        print(f'ERROR: {args.srt_dir} not found.', file=sys.stderr)
        sys.exit(1)

    if args.lang == 'auto':
        from lib.project_utils import detect_project_lang
        project_dir = os.path.dirname(os.path.abspath(args.srt_dir))
        lang = detect_project_lang(project_dir)
        print(f'[oped-fill] Auto-detected language: {lang}', file=sys.stderr)
    else:
        lang = args.lang

    filler = OpedFill(
        args.srt_dir,
        video_dir=args.video_dir,
        lang=lang,
        op_boundary=args.op_boundary,
        ed_boundary=args.ed_boundary,
        skip_step1=args.skip_step1,
        dry_run=args.dry_run,
    )

    result = filler.run()

    if args.output:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'\n[oped-fill] → {args.output}', file=sys.stderr)


if __name__ == '__main__':
    main()
