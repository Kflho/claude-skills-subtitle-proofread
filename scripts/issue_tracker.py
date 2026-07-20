#!/usr/bin/env python3
"""每集问题清单生成器 — 扫描 SRT，找出仅靠文本规则无法修复的乱码段。

输出: issues_EP{NNN}.json
用途: 供 Whisper 阶段（阶段六）逐集处理。
"""

import sys, os, re, json, argparse, glob

def find_unfixable_issues(srt_path, video_path=None):
    """扫描单个 SRT，返回无法用规则修复的问题列表。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
    from srt_utils import read_srt_file, parse_srt_cue

    lines = read_srt_file(srt_path)
    cues, max_end_ms, idx = [], 0, 0
    while idx < len(lines):
        cue, idx = parse_srt_cue(lines, idx)
        if cue is None: idx += 1; continue
        cues.append(cue)
        p = cue['end'].replace(',','.').split(':')
        ms = int(p[0])*3600000 + int(p[1])*60000 + int(float(p[2])*1000)
        if ms > max_end_ms: max_end_ms = ms

    issues = []
    for c in cues:
        text = c['text'].strip()
        if not text or text.startswith('['):
            continue
        if not re.search(r'[a-zA-Z]{2,}', text):
            continue
        p = c['start'].replace(',','.').split(':')
        ss = int(p[0])*3600 + int(p[1])*60 + float(p[2])
        if ss < 95 or ss > (max_end_ms/1000 - 120):
            continue  # 跳过 OP/ED 区域

        # 分类
        if re.fullmatch(r'[a-zA-Z\s\d]+', text):
            issue_type = 'garbled'
        elif re.search(r'[a-zA-Z]{2,}', text) and re.search(r'[぀-ヿ一-鿿]', text):
            issue_type = 'mixed_romaji'
        elif re.search(r'\b(i?phone|iphone|google|youtube|twitter)\b', text, re.I):
            issue_type = 'hallucination'
        else:
            issue_type = 'unintelligible'

        issues.append({
            'start': c['start'],
            'end': c['end'],
            'original_text': text,
            'type': issue_type,
            'note': ''
        })

    ep_num = os.path.basename(srt_path).split('-')[1].strip().split()[0] if '-' in os.path.basename(srt_path) else '???'

    result = {
        'episode': ep_num,
        'srt_file': srt_path,
        'video_file': video_path or '',
        'issue_count': len(issues),
        'issues': issues
    }
    return result


def main():
    parser = argparse.ArgumentParser(description='生成每集问题清单 JSON')
    parser.add_argument('--srt-dir', required=True, help='SRT 字幕目录')
    parser.add_argument('--video-dir', default='', help='视频文件目录（可选，用于自动匹配）')
    parser.add_argument('--output-dir', default='.', help='问题清单输出目录')
    parser.add_argument('--episode', '-e', help='仅处理指定集号')
    args = parser.parse_args()

    srt_files = sorted(glob.glob(os.path.join(args.srt_dir, '*.srt')))
    if args.episode:
        srt_files = [f for f in srt_files if args.episode in os.path.basename(f)]

    total_issues = 0
    for srt_path in srt_files:
        result = find_unfixable_issues(srt_path)
        ep = result['episode']

        # 尝试匹配视频文件（用 os.listdir 避免 glob 方括号坑）
        if args.video_dir:
            try:
                for fname in os.listdir(args.video_dir):
                    if ep in fname and fname.lower().endswith(('.mkv', '.mp4')):
                        result['video_file'] = os.path.join(args.video_dir, fname)
                        break
            except OSError:
                pass

        out_path = os.path.join(args.output_dir, f'issues_EP{ep}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f'EP{ep}: {result["issue_count"]}个问题 → {out_path}')
        total_issues += result['issue_count']

    print(f'\n总计: {len(srt_files)}集, {total_issues}个待Whisper处理的问题')
    if args.video_dir and total_issues > 0:
        missing = 0
        for srt_path in srt_files:
            ep = os.path.basename(srt_path).split('-')[1].strip().split()[0]
            out_path = os.path.join(args.output_dir, f'issues_EP{ep}.json')
            try:
                with open(out_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not data.get('video_file'):
                    missing += 1
            except (OSError, json.JSONDecodeError):
                missing += 1
        if missing:
            print(f'⚠ {missing}集未匹配到视频文件，Whisper步骤将跳过')


if __name__ == '__main__':
    main()
