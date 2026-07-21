#!/usr/bin/env python3
"""SRT batch translator using Baidu Translate API (通用翻译 API 标准版).

Free tier: 2M chars/month, 1 QPS. Works inside mainland China.

Setup:
  1. Register at https://fanyi-api.baidu.com/ → 通用翻译API (标准版, 免费)
  2. Create ~/.baidu_translate:
       BAIDU_APPID=你的APPID
       BAIDU_SECRET=你的密钥
  Or set env vars: BAIDU_APPID, BAIDU_SECRET

Usage:
  python translate_srt.py input.srt --to ja --output output.srt
  python translate_srt.py input.srt --to ja --output output.srt --source zh
  python translate_srt.py input.srt --to ja --output output.srt --batch 30 --delay 1.5
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse


# ═══════════════════════════════════════════════════════════════
# Credentials
# ═══════════════════════════════════════════════════════════════

def load_credentials():
    """Load Baidu API credentials from ~/.baidu_translate or env vars."""
    appid = os.environ.get('BAIDU_APPID', '')
    secret = os.environ.get('BAIDU_SECRET', '')
    endpoint = os.environ.get('BAIDU_API_ENDPOINT', '')

    config_path = os.path.expanduser('~/.baidu_translate')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('BAIDU_APPID='):
                    appid = appid or line.split('=', 1)[1]
                elif line.startswith('BAIDU_SECRET='):
                    secret = secret or line.split('=', 1)[1]
                elif line.startswith('BAIDU_ENDPOINT='):
                    endpoint = endpoint or line.split('=', 1)[1]

    return appid, secret, endpoint


# ═══════════════════════════════════════════════════════════════
# Baidu Translate API
# ═══════════════════════════════════════════════════════════════

BAIDU_API = 'https://fanyi-api.baidu.com/api/trans/vip/translate'

# Baidu language codes: zh=Chinese, en=English, jp=Japanese, kor=Korean, etc.
LANG_MAP = {'ja': 'jp', 'jp': 'jp', 'zh': 'zh', 'en': 'en', 'auto': 'auto'}


def get_api_endpoint():
    """Get API endpoint from env var or default."""
    return os.environ.get('BAIDU_API_ENDPOINT', BAIDU_API)


def baidu_translate(text, appid, secret, source='auto', target='ja', endpoint=None):
    """Translate a single text via Baidu API.

    Returns translated text string, or None on failure.
    """
    if endpoint is None:
        endpoint = get_api_endpoint()

    # Map language codes to Baidu format (ja→jp)
    source = LANG_MAP.get(source, source)
    target = LANG_MAP.get(target, target)

    salt = '172804'  # fixed salt
    sign_str = appid + text + salt + secret
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()

    params = {
        'q': text,
        'from': source,
        'to': target,
        'appid': appid,
        'salt': salt,
        'sign': sign,
    }

    url = endpoint + '?' + urllib.parse.urlencode(params)

    try:
        # SSL: only needed for HTTPS endpoints (skip for HTTP proxy)
        ctx = None
        if endpoint.startswith('https') and endpoint != BAIDU_API:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        if 'error_code' in data:
            error_msg = data.get('error_msg', 'unknown')
            print(f'  Baidu API error: {data["error_code"]} — {error_msg}', file=sys.stderr)
            return None

        return data['trans_result'][0]['dst']

    except urllib.error.HTTPError as e:
        print(f'  HTTP error {e.code}: {e.reason}', file=sys.stderr)
        return None
    except Exception as e:
        print(f'  Translation error: {e}', file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════════════
# SRT parsing
# ═══════════════════════════════════════════════════════════════

def parse_srt_cues(path):
    """Parse SRT into list of {index, start, end, text}."""
    cues = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        content = f.read()

    # Match SRT blocks
    pattern = re.compile(
        r'(\d+)\s*\n'
        r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*\n'
        r'((?:.+\n?)+?)(?=\n\d+\n|\n*\Z)',
        re.MULTILINE
    )

    for m in pattern.finditer(content):
        cues.append({
            'index': int(m.group(1)),
            'start': m.group(2).replace(',', '.'),
            'end': m.group(3).replace(',', '.'),
            'text': m.group(4).strip(),
        })

    return cues


def write_srt(path, cues):
    """Write cues to SRT file. Signature matches lib.whisper_utils.write_srt."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig') as f:
        for i, cue in enumerate(cues, 1):
            start = cue['start'].replace('.', ',')
            end = cue['end'].replace('.', ',')
            f.write(f'{i}\n')
            f.write(f'{start} --> {end}\n')
            f.write(f'{cue["text"]}\n\n')


# ═══════════════════════════════════════════════════════════════
# Language detection
# ═══════════════════════════════════════════════════════════════

def detect_source_language(cues, sample_size=10):
    """Naive language detection by character set sampling.

    Returns 'zh', 'en', 'ja', or 'auto'.
    """
    sample = ' '.join(c['text'] for c in cues[:sample_size] if c['text'].strip())

    cjk_count = len(re.findall(r'[一-鿿]', sample))
    kana_count = len(re.findall(r'[぀-ゟ゠-ヿ]', sample))
    latin_count = len(re.findall(r'[a-zA-Z]', sample))

    total = len(sample.replace(' ', ''))
    if total == 0:
        return 'auto'

    if (cjk_count + kana_count) / max(total, 1) > 0.3:
        if kana_count > cjk_count * 0.5:
            return 'ja'
        return 'zh'

    if latin_count / max(total, 1) > 0.4:
        return 'en'

    return 'auto'


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='SRT batch translator — Baidu Translate API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Setup:
  1. Register: https://fanyi-api.baidu.com/ → 通用翻译API (标准版免费)
  2. Create ~/.baidu_translate with BAIDU_APPID and BAIDU_SECRET
  3. Or set environment variables: BAIDU_APPID, BAIDU_SECRET

Examples:
  python translate_srt.py ref.srt --to ja --output translated.srt
  python translate_srt.py ref.srt --to ja --output translated.srt --source zh
        """
    )
    parser.add_argument('input', help='Input SRT file')
    parser.add_argument('--to', default='ja', help='Target language (default: ja)')
    parser.add_argument('--source', default='auto', help='Source language (default: auto-detect)')
    parser.add_argument('--output', required=True, help='Output SRT path')
    parser.add_argument('--batch', type=int, default=50,
                        help='Cues per batch (default: 50)')
    parser.add_argument('--delay', type=float, default=1.2,
                        help='Delay between batches in seconds (default: 1.2, for 1 QPS)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume: skip already-translated cues in output file')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count cues and estimate cost, no actual translation')
    parser.add_argument('--endpoint', default=None,
                        help=f'API endpoint override (default: {BAIDU_API}). '
                             'Use http://127.0.0.1:8888/api/trans/vip/translate for proxy.')
    args = parser.parse_args()

    # Load credentials
    appid, secret, config_endpoint = load_credentials()
    if not appid or not secret:
        print('ERROR: Baidu API credentials not found.', file=sys.stderr)
        print('Create ~/.baidu_translate with BAIDU_APPID and BAIDU_SECRET,', file=sys.stderr)
        print('or set BAIDU_APPID and BAIDU_SECRET environment variables.', file=sys.stderr)
        print('Register at: https://fanyi-api.baidu.com/', file=sys.stderr)
        sys.exit(1)

    # Endpoint: CLI arg > env var > config file > default
    if args.endpoint is None:
        args.endpoint = config_endpoint or BAIDU_API

    # Parse input
    cues = parse_srt_cues(args.input)
    print(f'Input: {args.input} ({len(cues)} cues)', file=sys.stderr)

    if not cues:
        print('No cues found.', file=sys.stderr)
        sys.exit(1)

    # Auto-detect source language
    if args.source == 'auto':
        detected = detect_source_language(cues)
        if detected != 'auto':
            args.source = detected
            print(f'Detected source: {args.source}', file=sys.stderr)

    # Resume: load already translated
    translated_set = set()
    if args.resume and os.path.exists(args.output):
        existing = parse_srt_cues(args.output)
        translated_set = {c['start'] for c in existing}
        print(f'Resume: {len(translated_set)} already translated', file=sys.stderr)

    # Filter untranslated cues
    pending = [c for c in cues if c['start'] not in translated_set]

    if args.dry_run:
        total_chars = sum(len(c['text']) for c in pending)
        batches = (len(pending) + args.batch - 1) // args.batch
        print(f'\nDRY RUN:', file=sys.stderr)
        print(f'  Cues to translate: {len(pending)}/{len(cues)}', file=sys.stderr)
        print(f'  Characters: {total_chars}', file=sys.stderr)
        print(f'  Batches: {batches} (×{args.batch})', file=sys.stderr)
        print(f'  Est. time: {batches * args.delay:.0f}s', file=sys.stderr)
        print(f'  Free quota: 2,000,000 chars/month', file=sys.stderr)
        return

    if not pending:
        print('All cues already translated.', file=sys.stderr)
        return

    # Batch translate
    results = {c['start']: c for c in cues if c['start'] in translated_set}
    failed = 0

    for batch_start in range(0, len(pending), args.batch):
        batch = pending[batch_start:batch_start + args.batch]
        batch_num = batch_start // args.batch + 1
        total_batches = (len(pending) + args.batch - 1) // args.batch

        for cue in batch:
            text = cue['text']
            if not text.strip():
                results[cue['start']] = {**cue, 'text': ''}
                continue

            translated = baidu_translate(text, appid, secret, source=args.source,
                                          target=args.to, endpoint=args.endpoint)

            if translated is not None:
                results[cue['start']] = {**cue, 'text': translated}
            else:
                # Keep original text on failure
                results[cue['start']] = {**cue, 'text': text}
                failed += 1

        # Progress
        done = batch_start + len(batch)
        pct = done * 100 // len(pending)
        print(f'\r  [{done}/{len(pending)}] {pct}%  failed: {failed}', end='', file=sys.stderr)

        # Rate limit
        if done < len(pending):
            time.sleep(args.delay)

    print(file=sys.stderr)

    # Write output (preserve original order)
    output_cues = [results[c['start']] for c in cues if c['start'] in results]
    write_srt(args.output, output_cues)

    print(f'Output: {args.output} ({len(output_cues)} cues)', file=sys.stderr)
    if failed:
        print(f'WARNING: {failed} cues failed translation (kept original)', file=sys.stderr)

    # Estimate character usage
    total_chars = sum(len(c['text']) for c in pending)
    print(f'Characters used: ~{total_chars} / 2,000,000 free quota', file=sys.stderr)


if __name__ == '__main__':
    main()
