#!/usr/bin/env python3
"""用音频指纹定位每集的 OP / ED 时间段（不依赖字幕文本）。

为什么这么做：同一部片子的 OP/ED **曲子**通常每集完全一样（换的只是画面），
所以拿参考集的 OP/ED 音频做成频谱指纹，在每集开头/结尾滑窗做归一化互相关，
峰值位置就是这一段在本集的起点。比「先翻译、再按唱词猜窗口」稳、便宜，
也不受 ASR 把唱词转成乱码的影响。

流程（推荐）：本工具定时间 → 抽边界帧（`--frames-dir`）人工/画面复核 → 再改字幕。

用法：
  # 参考集：EP001 的 OP 在第 38–112 秒、ED 在 1441–1490 秒
  python scripts/audio/oped_boundaries.py --video-dir "<视频目录>" \
    --op-ref "<EP001.avi>@38-112" --ed-ref "<EP001.avi>@1441-1490" \
    --output data/temp/oped_windows.json --frames-dir data/temp/oped_frames

  # 只想看某几集
  python scripts/audio/oped_boundaries.py ... --episodes 1-5

输出 JSON：{"EP001": {"op": {"start": 38.05, "end": 112.0, "score": 0.98}, "ed": ...}}
score 低于 --min-score 的区域记为 null，表示没找到（换剪辑/换曲/没有该段）。

注意：只做定位，不改字幕。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import wave

try:
    import numpy as np
except ImportError:  # pragma: no cover - 环境缺失时给清楚的话
    np = None

SR = 16000
HOP = 0.05        # 50 ms → 20 帧/秒
WIN = 0.064       # 64 ms 窗
BANDS = 24
F_LO, F_HI = 60.0, 7000.0
EP_RE = re.compile(r'第\s*(\d{1,3})\s*話|EP(\d{1,3})|(?:^|\D)(\d{1,3})(?:話|\.)', re.I)


# ── 参考段参数解析 ───────────────────────────────────────────────

def parse_ref(spec: str) -> tuple[str, float, float]:
    """'path.avi@38-112' → (path, 38.0, 112.0)"""
    if '@' not in spec:
        raise ValueError(f'reference needs path@START-END, got {spec!r}')
    path, rng = spec.rsplit('@', 1)
    m = re.fullmatch(r'\s*([\d.]+)\s*[-:]\s*([\d.]+)\s*', rng)
    if not m:
        raise ValueError(f'bad time range {rng!r} (want 38-112)')
    start, end = float(m.group(1)), float(m.group(2))
    if end <= start:
        raise ValueError(f'empty range {rng!r}')
    return path, start, end


# ── 音频读取与特征 ───────────────────────────────────────────────

def read_wav(path: str) -> 'np.ndarray':
    """16-bit PCM wav → float32 mono [-1, 1]。"""
    if np is None:
        raise RuntimeError('numpy is required for oped_boundaries')
    with wave.open(path, 'rb') as w:
        n, sw, ch, sr = (w.getnframes(), w.getsampwidth(),
                         w.getnchannels(), w.getframerate())
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f'only 16-bit PCM supported, got {sw * 8}-bit: {path}')
    data = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        raise ValueError(f'expected {SR} Hz, got {sr}: {path}')
    return data


def extract_wav(video: str, out_wav: str, ffmpeg: str = 'ffmpeg') -> str:
    os.makedirs(os.path.dirname(out_wav) or '.', exist_ok=True)
    cmd = [ffmpeg, '-y', '-v', 'error', '-i', video, '-vn',
           '-ac', '1', '-ar', str(SR), '-acodec', 'pcm_s16le', out_wav]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)
    return out_wav


def band_features(samples: 'np.ndarray') -> 'np.ndarray':
    """波形 → (BANDS, frames) 的对数频带能量，逐帧 L2 归一化（静音帧置零）。"""
    if np is None:
        raise RuntimeError('numpy is required for oped_boundaries')
    win_n = int(WIN * SR)
    hop_n = int(HOP * SR)
    if len(samples) < win_n:
        return np.zeros((BANDS, 0), dtype=np.float32)
    frames = 1 + (len(samples) - win_n) // hop_n
    window = np.hanning(win_n).astype(np.float32)
    freqs = np.fft.rfftfreq(win_n, 1.0 / SR)
    edges = np.logspace(np.log10(F_LO), np.log10(min(F_HI, SR / 2 * 0.95)),
                        BANDS + 1)
    feats = np.zeros((BANDS, frames), dtype=np.float32)
    chunk = 2048
    for start in range(0, frames, chunk):
        stop = min(start + chunk, frames)
        idx = (np.arange(start, stop)[:, None] * hop_n
               + np.arange(win_n)[None, :])
        spec = np.abs(np.fft.rfft(samples[idx] * window, axis=1))
        for i in range(BANDS):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            if mask.any():
                feats[i, start:stop] = spec[:, mask].mean(axis=1)
    feats = np.log1p(feats)
    norms = np.linalg.norm(feats, axis=0)
    good = norms > 1e-6
    feats[:, good] /= norms[good]
    feats[:, ~good] = 0.0
    return feats


def slice_features(feats: 'np.ndarray', start_s: float,
                   end_s: float) -> 'np.ndarray':
    i0 = max(0, int(round(start_s / HOP)))
    i1 = min(feats.shape[1], int(round(end_s / HOP)))
    return feats[:, i0:i1]


# ── 匹配 ─────────────────────────────────────────────────────────

def locate(ref: 'np.ndarray', episode: 'np.ndarray',
           search: tuple[float, float] | None = None,
           min_score: float = 0.0) -> dict | None:
    """在 episode 特征里找 ref 出现的位置。

    search 是搜索区间（秒），缺省搜全程。返回 {'start', 'end', 'score'}；
    最高分低于 min_score 返回 None。
    """
    if np is None:
        raise RuntimeError('numpy is required for oped_boundaries')
    L = ref.shape[1]
    N = episode.shape[1]
    if L < 20 or N < L:
        return None
    lo = 0
    hi = N - L
    if search:
        lo = max(0, int(round(search[0] / HOP)))
        hi = min(hi, int(round(search[1] / HOP)))
    if hi < lo:
        return None

    num = np.zeros(N - L + 1, dtype=np.float32)
    for band in range(ref.shape[0]):
        num += np.correlate(episode[band], ref[band], mode='valid')
    e_frames = (episode ** 2).sum(axis=0)
    slide = np.convolve(e_frames, np.ones(L, dtype=np.float32), mode='valid')
    ref_e = float((ref ** 2).sum())
    denom = np.sqrt(np.maximum(slide * ref_e, 1e-9))
    score = num / denom

    seg = score[lo:hi + 1]
    if seg.size == 0:
        return None
    best = int(np.argmax(seg)) + lo
    best_score = float(seg[int(np.argmax(seg))])
    if best_score < min_score:
        return None
    start = best * HOP
    return {'start': round(start, 3), 'end': round(start + L * HOP, 3),
            'score': round(best_score, 4)}


# ── 视频清单 ─────────────────────────────────────────────────────

def ep_number(name: str) -> int | None:
    m = EP_RE.search(name)
    if not m:
        return None
    for g in m.groups():
        if g:
            return int(g)
    return None


def list_videos(video_dir: str, episodes: list[int] | None = None) -> list[tuple[int, str]]:
    out = []
    for fn in sorted(os.listdir(video_dir)):
        if not fn.lower().endswith(('.avi', '.mkv', '.mp4', '.ts', '.m2ts')):
            continue
        n = ep_number(fn)
        if n is None:
            continue
        if episodes and n not in episodes:
            continue
        out.append((n, os.path.join(video_dir, fn)))
    return sorted(out)


def parse_episodes(spec: str) -> list[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.replace('EP', '').split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def dump_frames(video: str, region: dict, out_dir: str, ep: str, tag: str,
                ffmpeg: str) -> list[str]:
    """在区域的起点/中点/终点各抽一帧，供画面复核。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    start, end = region['start'], region['end']
    for label, t in (('start', start + 0.6), ('mid', (start + end) / 2),
                     ('end', end - 1.2)):
        if t < 0:
            continue
        path = os.path.join(out_dir, f'{ep}_{tag}_{label}.png')
        cmd = [ffmpeg, '-y', '-v', 'error', '-ss', f'{t:.2f}', '-i', video,
               '-frames:v', '1', path]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
            paths.append(path)
        except subprocess.CalledProcessError:
            pass
    return paths


# ── CLI ─────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--video-dir', required=True)
    ap.add_argument('--op-ref', default='', help='"<参考视频>@START-END"')
    ap.add_argument('--ed-ref', default='', help='"<参考视频>@START-END"')
    ap.add_argument('--output', '-o', default='data/temp/oped_windows.json')
    ap.add_argument('--episodes', '-e', default='',
                    help='EP001-EP005 / 1-5 / 1,3,5')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--min-score', type=float, default=0.55,
                    help='低于此分记为 null（默认 0.55）')
    ap.add_argument('--op-search', default='0-240',
                    help='OP 搜索区间（秒），默认 0-240')
    ap.add_argument('--ed-search', default='',
                    help='ED 搜索区间（秒）；空 = 全片最后 360 秒')
    ap.add_argument('--frames-dir', default='',
                    help='给了就抽边界帧（start/mid/end）供画面复核')
    ap.add_argument('--work-dir', default='temp/oped_wav')
    ap.add_argument('--ffmpeg', default=os.environ.get('FFMPEG', 'ffmpeg'))
    ap.add_argument('--keep-wav', action='store_true')
    args = ap.parse_args(argv)

    if np is None:
        print('error: numpy is required (pip install numpy)', file=sys.stderr)
        return 2
    if not args.op_ref and not args.ed_ref:
        print('error: need --op-ref and/or --ed-ref', file=sys.stderr)
        return 2

    refs = {}
    for tag, spec in (('op', args.op_ref), ('ed', args.ed_ref)):
        if not spec:
            continue
        path, start, end = parse_ref(spec)
        print(f'[oped] ref {tag}: {os.path.basename(path)} {start}-{end}s',
              file=sys.stderr)
        wav = extract_wav(path, os.path.join(args.work_dir, f'ref_{tag}.wav'),
                          args.ffmpeg)
        samples = read_wav(wav)
        feats = band_features(samples)
        refs[tag] = slice_features(feats, start, end)
        print(f'[oped] ref {tag} frames: {refs[tag].shape[1]}', file=sys.stderr)

    episodes = parse_episodes(args.episodes)
    videos = list_videos(args.video_dir, episodes)
    if args.limit:
        videos = videos[:args.limit]
    if not videos:
        print(f'error: no videos in {args.video_dir}', file=sys.stderr)
        return 1

    op_search = tuple(float(x) for x in args.op_search.split('-'))
    result: dict[str, dict] = {}
    for n, video in videos:
        ep = f'EP{n:03d}'
        wav = extract_wav(video, os.path.join(args.work_dir, f'{ep}.wav'),
                          args.ffmpeg)
        feats = band_features(read_wav(wav))
        total = feats.shape[1] * HOP
        rec = {'duration': round(total, 2)}
        if 'op' in refs:
            rec['op'] = locate(refs['op'], feats, op_search, args.min_score)
        if 'ed' in refs:
            search = ((float(args.ed_search.split('-')[0]),
                       float(args.ed_search.split('-')[1]))
                      if args.ed_search else (max(0.0, total - 360), total))
            rec['ed'] = locate(refs['ed'], feats, search, args.min_score)
        result[ep] = rec
        fmt = lambda r: (f"{r['start']:.1f}-{r['end']:.1f}s ({r['score']:.2f})"
                         if r else 'null')
        print(f"[oped] {ep} op={fmt(rec.get('op'))} ed={fmt(rec.get('ed'))}",
              file=sys.stderr)
        if args.frames_dir:
            for tag in ('op', 'ed'):
                if rec.get(tag):
                    dump_frames(video, rec[tag], args.frames_dir, ep, tag,
                                args.ffmpeg)
        if not args.keep_wav:
            try:
                os.remove(wav)
            except OSError:
                pass

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    ok = sum(1 for r in result.values()
             for t in ('op', 'ed') if r.get(t))
    print(f'[oped] wrote {args.output}: {len(result)} eps, {ok} regions located',
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
