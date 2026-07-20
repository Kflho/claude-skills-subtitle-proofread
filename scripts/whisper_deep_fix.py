#!/usr/bin/env python3
"""Whisper 深度碎片修复 — 对 Tier 1+2 后剩余未修复碎片进行拆分重处理。

策略:
  1. 读取统一报告步骤16（待人工审查条目）
  2. 找到对应的视频切片 mp4
  3. ffmpeg silencedetect 找静音断点（≥1.5s 静音）
  4. 在断点处拆分子片段
  5. 每个子片段单独喂 Whisper（retry 模型，-nth 0.3）
  6. 结果对齐回填，更新 SRT 和统一报告

用法:
  python whisper_deep_fix.py \
    --report reports/问题解决报告.md \
    --srt-dir 日语ai生成字幕/ \
    --video-dir reports/manual-review/ \
    --whisper-cli D:/.../whisper-cli.exe \
    --model D:/.../large-v3-q5_0.bin \
    --json
"""

import sys, os, re, subprocess, json, argparse, tempfile, io, time

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

SILENCE_MIN = 1.5       # 静音阈值（秒），超过此值视为断点
PADDING = 0.3            # 切分后前后各加的 padding（秒）


def _to_seconds(tc):
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _format_tc(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')


def detect_silence_breaks(audio_path, dur):
    """用 ffmpeg silencedetect 找静音断点。返回 [break_time, ...]（秒）。"""
    cmd = [
        'ffmpeg', '-i', audio_path,
        '-af', f'silencedetect=n=-30dB:d={SILENCE_MIN}',
        '-f', 'null', '-',
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    breaks = []
    for line in (proc.stderr or '').split('\n'):
        m = re.search(r'silence_end:\s*([\d.]+)', line)
        if m:
            t = float(m.group(1))
            if PADDING < t < dur - PADDING:
                breaks.append(t)
    return sorted(set(round(b, 1) for b in breaks))


def split_fragment(audio_path, dur, breaks):
    """根据断点将音频拆分为子片段。返回 [(ss, es), ...] 秒数对。"""
    if not breaks:
        return [(0, dur)]

    segments = []
    prev = 0
    for b in breaks:
        if b - prev >= 1.0:  # 至少 1 秒有效语音
            segments.append((max(0, prev - PADDING), min(dur, b + PADDING)))
        prev = b
    # 最后一段
    if dur - prev >= 1.0:
        segments.append((max(0, prev - PADDING), dur))

    return segments if segments else [(0, dur)]


def run_whisper_segment(audio_path, whisper_cli, model_path):
    """对单段音频运行 whisper，返回文本（或 None）。"""
    cmd = [
        whisper_cli, '-m', model_path, '-f', audio_path, '-l', 'ja',
        '-t', '4', '-p', '1', '-bs', '5', '-bo', '4',
        '-oj', '-of', audio_path + '.whisper', '--print-progress',
        '-nth', '0.3',     # 极低阈值，最大程度捕捉语音
        '-mc', '0',
        '-sns',
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    json_path = audio_path + '.whisper.json'

    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        if os.path.exists(json_path):
            os.remove(json_path)
        return None
    os.remove(json_path)

    # 取第一个有效 segment 文本
    for seg in data.get('transcription', []):
        text = seg.get('text', '').strip()
        if text and (re.search(r'[぀-ヿ]', text) or re.search(r'[一-鿿]', text)):
            return text

    return None


def find_srt_file(srt_dir, ep_tag):
    """根据集号找到 SRT 文件。"""
    ep_num = ep_tag.replace('EP', '')
    for fname in os.listdir(srt_dir):
        if fname.endswith('.srt') and (ep_tag in fname or ep_num in fname):
            return os.path.join(srt_dir, fname)
    return None


def apply_single_fix(srt_path, start_tc, replacement):
    """将单条修复写入 SRT 中对应时间码的 cue。"""
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(srt_path)
    cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None:
            idx += 1
            continue
        cues.append(cue)

    start_s = _to_seconds(start_tc)
    for cue in cues:
        cs = _to_seconds(cue['start'])
        if abs(cs - start_s) < 0.5:
            cue['text'] = replacement
            break
    else:
        return False

    with open(srt_path, 'w', encoding='utf-8-sig') as f:
        for i, cue in enumerate(cues, 1):
            f.write(f'{i}\n')
            f.write(f'{cue["start"]} --> {cue["end"]}\n')
            f.write(f'{cue["text"]}\n\n')
    return True


def main():
    parser = argparse.ArgumentParser(description='Whisper 深度碎片修复')
    parser.add_argument('--report', required=True, help='统一问题解决报告路径')
    parser.add_argument('--srt-dir', required=True, help='SRT 字幕目录')
    parser.add_argument('--video-dir', required=True, help='视频切片目录（manual-review/）')
    parser.add_argument('--whisper-cli', required=True)
    parser.add_argument('--model', required=True, help='retry 模型路径（large-v3）')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--episode', '-e', help='仅处理指定集号（如 EP031）')
    args = parser.parse_args()

    from update_report import read_report, update_entry_status, upsert_entries

    data = read_report(args.report)
    step16 = data.get(16, [])
    if not step16:
        print('步骤16无待处理条目')
        return

    # 按集分组
    by_ep = {}
    for entry in step16:
        if entry.get('status') != '⬜':
            continue
        ep = entry['ep']
        if args.episode and ep != args.episode:
            continue
        if ep not in by_ep:
            by_ep[ep] = []
        by_ep[ep].append(entry)

    print(f'待深度处理: {sum(len(v) for v in by_ep.values())} 条 ({len(by_ep)} 集)')

    total_fixed = 0
    total_unmatched = 0

    for ep, entries in sorted(by_ep.items()):
        srt_path = find_srt_file(args.srt_dir, ep)
        if not srt_path:
            print(f'  ⚠ {ep}: 未找到 SRT')
            continue

        for entry in entries:
            time_start = entry['time']
            # 找到对应的视频切片
            safe_tc = time_start.replace(',', '-').replace('.', '-').replace(':', '-')
            # 搜索匹配的 mp4 文件
            clip = None
            for fname in os.listdir(args.video_dir):
                if fname.startswith(ep) and safe_tc[:12] in fname and fname.endswith('.mp4'):
                    clip = os.path.join(args.video_dir, fname)
                    break
            if not clip:
                total_unmatched += 1
                continue

            # 提取音频
            tmpdir = tempfile.mkdtemp()
            tmp_audio = os.path.join(tmpdir, 'frag.wav')
            try:
                subprocess.run([
                    'ffmpeg', '-y', '-i', clip, '-vn',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                    tmp_audio,
                ], capture_output=True, check=True)
            except subprocess.CalledProcessError:
                import shutil; shutil.rmtree(tmpdir)
                total_unmatched += 1
                continue

            # 获取时长
            probe = subprocess.run([
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'csv=p=0', tmp_audio,
            ], capture_output=True, text=True)
            dur = float(probe.stdout.strip()) if probe.stdout.strip() else 0

            if dur < 1.0:
                import shutil; shutil.rmtree(tmpdir)
                total_unmatched += 1
                continue

            # 检测静音断点 + 拆分
            breaks = detect_silence_breaks(tmp_audio, dur)
            segments = split_fragment(tmp_audio, dur, breaks)

            if args.json and not args.dry_run:
                print(f'  {ep} [{time_start}] dur={dur:.0f}s → {len(segments)}段 (breaks={breaks})')

            # 逐段转录
            best_text = None
            for i, (ss, es) in enumerate(segments):
                seg_audio = os.path.join(tmpdir, f'seg{i:03d}.wav')
                try:
                    subprocess.run([
                        'ffmpeg', '-y', '-ss', str(ss), '-t', str(es - ss),
                        '-i', tmp_audio, '-c', 'copy', seg_audio,
                    ], capture_output=True, check=True)
                except subprocess.CalledProcessError:
                    continue

                text = run_whisper_segment(seg_audio, args.whisper_cli, args.model)
                if text and (not best_text or len(text) > len(best_text)):
                    best_text = text

            # 应用修复
            if best_text and best_text.strip():
                if not args.dry_run:
                    ok = apply_single_fix(srt_path, time_start, best_text)
                    if ok:
                        update_entry_status(args.report, step=16, ep=ep,
                                          time=time_start, corrected=best_text, status='✅')
                total_fixed += 1
                if args.json:
                    print(f'    → "{best_text[:60]}"')
            else:
                total_unmatched += 1

            import shutil; shutil.rmtree(tmpdir)

    print(f'\n深度修复完成: {total_fixed}✅ / {total_unmatched}⬜')

    if args.dry_run:
        print('[DRY-RUN] 未实际写入')


if __name__ == '__main__':
    main()
