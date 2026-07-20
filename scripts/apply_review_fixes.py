#!/usr/bin/env python3
"""读取人工审查清单，批量修改 SRT 字幕，支持多行修正 + VAD 打轴。

清单格式：
  EP019 | 00:08:10.569 ~ 00:08:42.810 | EP019_00-08-10_to_00-08-42.mp4
  残留: えっはりなキャイー nd / hani / pa / uh uh
  修正:
  メンバーが怪物を生き返らせた
  山を爆破したんだ
  これは大変なことになった

修正可写多行（每行一句），脚本自动检测人声区间分配时间轴。
"""

import sys, os, re, argparse, subprocess, json, tempfile

# 脚本目录加入 path
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)


def parse_checklist(path):
    """解析审查清单。返回 [(ep, start, end, original, corrected_lines, clip_file)]。"""
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 按条目拆分：先找所有标题行，再按标题行分割
    header_pattern = re.compile(r'^(EP)?(\d+)\s*\|\s*([\d:,.-]+)\s*~\s*([\d:,.-]+)\s*\|\s*(.+\.mp4)')
    raw_lines = content.split('\n')

    # 找所有标题行的位置
    entries = []
    seen = set()
    for i, line in enumerate(raw_lines):
        m = header_pattern.match(line.strip())
        if not m: continue
        ep = f'EP{m.group(2)}' if not m.group(1) else f'{m.group(1)}{m.group(2)}'
        start = m.group(3)
        end = m.group(4)
        clip_file = m.group(5).strip()

        # 收集后续行直到遇到下一个标题行、---、或空行后的非内容行
        original = ''
        corrected_lines = []
        in_correction = False
        j = i + 1
        while j < len(raw_lines):
            s = raw_lines[j].strip()
            # 遇到新标题行或 --- 就停止
            if header_pattern.match(s) or s == '---':
                break
            if s.startswith('残留:'):
                original = s[3:].strip()
                in_correction = False
            elif s.startswith('修正:'):
                rest = s[3:].strip()
                if rest and rest not in ('_', '___'):
                    corrected_lines.append(rest)
                in_correction = True
            elif in_correction and s and not s.startswith('#') and not s.startswith('>'):
                if s not in ('_', '___'):
                    corrected_lines.append(s)
            elif not in_correction and s == '':
                # 空行：如果还没开始修正且后面不是修正内容，可能是旧条目的结束
                pass
            j += 1

        if corrected_lines and (ep, start) not in seen:
            seen.add((ep, start))
            entries.append((ep, start, end, original, corrected_lines, clip_file))

    return entries


def detect_speech_segments(video_path, start_s, end_s):
    """用 whisper.cpp VAD 检测人声区间（抗音乐干扰）。返回 [(ss, es), ...] 秒数。"""
    dur = end_s - start_s
    tmp = tempfile.mktemp(suffix='.wav')
    # 提取音频片段
    subprocess.run(['ffmpeg', '-y', '-ss', str(start_s), '-t', str(dur),
                    '-i', video_path, '-vn', '-ac', '1', '-ar', '16000', tmp],
                   capture_output=True, check=True)

    # 用 whisper.cpp 检测语音时间戳（只跑 VAD，不管文本质量）
    whisper_cli = _find_whisper_cli()
    if not whisper_cli:
        # 回退：整个区间算一段
        os.remove(tmp)
        return [(0, dur)]

    model = _find_model()
    result = subprocess.run(
        [whisper_cli, '-m', model, '-f', tmp,
         '-l', 'ja', '-t', '4', '-p', '1', '-bs', '5', '-bo', '1',
         '-oj', '-of', tmp + '.whisper', '--print-progress',
         '-nf', '-nth', '0.3']  # -sns 已移除：VAD 模式下非语音 token 抑制会制造幻觉,
        capture_output=True, text=True
    )

    # 解析 whisper JSON 获取时间戳
    json_path = tmp + '.whisper.json'
    segments = [(0, dur)]
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        segs = []
        for seg in data.get('transcription', []):
            ts = seg.get('timestamps', {})
            t_from = ts.get('from', '00:00:00,000')
            t_to = ts.get('to', '00:00:01,000')
            ss_seg = _parse_ts(t_from)
            es_seg = _parse_ts(t_to)
            if es_seg - ss_seg >= 0.3:
                segs.append((ss_seg, es_seg))
        if segs:
            segments = segs
        os.remove(json_path)

    # 清理
    for f in [tmp, tmp + '.whisper.json']:
        if os.path.exists(f): os.remove(f)
    return segments


def _parse_ts(ts):
    ts = ts.replace(',', '.')
    parts = ts.split(':')
    return int(parts[0])*3600 + int(parts[1])*60 + float(parts[2])


def _find_whisper_cli():
    """扫描常见安装路径。"""
    import glob as _g
    candidates = [
        'D:/software/video/whisper-cublas-12.4.0-bin-x64/whisper-cli.exe',
        'D:/software/video/whisper-cublas-*/whisper-cli.exe',
        'D:/software/video/whisper.cpp/whisper-cli.exe',
    ]
    for c in candidates:
        if '*' in c:
            matches = _g.glob(c)
            if matches: return matches[0]
        elif os.path.exists(c):
            return c
    return None


def _find_model():
    """找 kotoba 模型，找不到用 large-v3。"""
    import glob as _g
    for pattern in [
        'D:/software/video/whisper-cublas-*/models/ggml-kotoba-whisper-v2.0-q5_0.bin',
        'D:/software/video/whisper-cublas-*/models/ggml-large-v3-q5_0.bin',
    ]:
        matches = _g.glob(pattern)
        if matches: return matches[0]
    return None


def _to_seconds(tc):
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0])*3600 + int(parts[1])*60 + float(parts[2])


def _format_tc(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h:02d}:{m:02d}:{s:06.3f}'.replace('.', ',')


def apply_to_srt(srt_path, start_tc, end_tc, corrected_lines, video_path):
    """替换时间范围内的 cue，用 VAD 为多行修正分配时间轴。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(srt_path)
    cues, idx = [], 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None: idx += 1; continue
        cues.append(cue)

    start_s = _to_seconds(start_tc)
    end_s = _to_seconds(end_tc)

    # 找到要删除的 cues（原始时间范围，后续会扩展到新 cue 的覆盖范围）
    matched, keep = [], []
    for c in cues:
        cs = _to_seconds(c['start'])
        ce = _to_seconds(c['end'])
        if start_s - 0.5 <= cs and ce <= end_s + 0.5:
            matched.append(c)
        else:
            keep.append(c)

    if not matched:
        return False, f'未找到 [{start_tc} ~ {end_tc}] 范围内的 cue'

    # VAD 检测人声区间（video_path 是切片，从 0 开始）
    import subprocess as _sp
    probe = _sp.run(['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                     '-of', 'csv=p=0', video_path], capture_output=True, text=True)
    clip_dur = float(probe.stdout.strip()) if probe.stdout.strip() else (end_s - start_s)
    speech_segs = detect_speech_segments(video_path, 0, clip_dur)

    # 分配修正行到人声区间
    new_cues = []
    if len(corrected_lines) <= len(speech_segs):
        # 每行分配一个区间
        for i, text in enumerate(corrected_lines):
            ss, es = speech_segs[i]
            new_cues.append({
                'start': _format_tc(start_s + ss),
                'end': _format_tc(start_s + es),
                'text': text
            })
    else:
        # 修正行多于区间，均匀分配
        chunk = len(corrected_lines) / len(speech_segs)
        for i, (ss, es) in enumerate(speech_segs):
            seg_lines = corrected_lines[int(i*chunk):int((i+1)*chunk)]
            if seg_lines:
                new_cues.append({
                    'start': _format_tc(start_s + ss),
                    'end': _format_tc(start_s + es),
                    'text': ' '.join(seg_lines)
                })

    # 清除与新 cue 时间重叠的旧乱码（VAD 可能扩展了时间范围）
    new_start = min(_to_seconds(c['start']) for c in new_cues)
    new_end = max(_to_seconds(c['end']) for c in new_cues)
    keep = [c for c in keep
            if not (_to_seconds(c['start']) <= new_end + 1 and
                    _to_seconds(c['end']) >= new_start - 1)]

    # 重建 cue 列表
    all_cues = keep + new_cues
    all_cues.sort(key=lambda c: _to_seconds(c['start']))

    with open(srt_path, 'w', encoding='utf-8-sig') as f:
        for i, c in enumerate(all_cues, 1):
            f.write(f'{i}\n')
            f.write(f'{c["start"]} --> {c["end"]}\n')
            f.write(f'{c["text"]}\n\n')

    return True, f'{len(matched)} cues → {len(new_cues)} cues (VAD: {len(speech_segs)}人声段)'


def main():
    parser = argparse.ArgumentParser(description='根据人工审查清单批量修改 SRT（支持多行修正+VAD打轴）')
    parser.add_argument('checklist', help='审查清单文件路径（review-checklist.md）')
    parser.add_argument('--srt-dir', required=True, help='SRT 字幕目录')
    parser.add_argument('--video-dir', help='视频目录（用于 VAD，默认同 --srt-dir）')
    parser.add_argument('--dry-run', action='store_true', help='仅预览')
    parser.add_argument('--update-report', help='统一问题解决报告路径（回写步骤16状态）')
    args = parser.parse_args()

    entries = parse_checklist(args.checklist)
    print(f'读取 {len(entries)} 条已填写修正')

    # 视频目录默认同 checklist 的 manual-review 目录
    video_dir = args.video_dir or os.path.dirname(args.checklist)

    applied, failed = 0, 0
    for ep, start, end, orig, corr_lines, clip in entries:
        ep_num = ep.replace('EP', '')
        srt_files = [f for f in os.listdir(args.srt_dir)
                     if f.endswith('.srt') and (ep in f or ep_num in f)]
        if not srt_files:
            print(f'⚠ {ep}: 未找到 SRT 文件')
            failed += 1
            continue

        srt_path = os.path.join(args.srt_dir, srt_files[0])
        video_path = os.path.join(video_dir, clip)

        if args.dry_run:
            print(f'  {ep} [{start}~{end}]: {len(corr_lines)}行 → "{orig[:40]}"')
            for l in corr_lines:
                print(f'    → {l[:60]}')
        else:
            ok, msg = apply_to_srt(srt_path, start, end, corr_lines, video_path)
            if ok:
                print(f'  ✓ {ep} [{start}]: {msg}')
                applied += 1
                # 回写统一报告步骤16
                if args.update_report:
                    from update_report import update_entry_status as _upd
                    corr_text = ' '.join(corr_lines) if corr_lines else '(已删除)'
                    is_deleted = not corr_lines or corr_lines == ['(已删除)']
                    _upd(args.update_report, step=16, ep=ep, time=start,
                         corrected=corr_text,
                         status='🗑️' if is_deleted else '✅')
            else:
                print(f'  ✗ {ep} [{start}]: {msg}')
                failed += 1

    if args.dry_run:
        print(f'\n预览: {len(entries)} 条（--dry-run）')
    else:
        print(f'\n完成: {applied} 成功 / {failed} 失败')


if __name__ == '__main__':
    main()
