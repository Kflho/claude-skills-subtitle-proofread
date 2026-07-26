#!/usr/bin/env python3
"""Batch classify unknown_suspect terms from candidates.json via LLM API.

Reads temp/scans/candidates.json, batches terms to LLM for classification
as proper_noun or common_word, writes results and auto-generates blacklist + mappings.

Usage:
  python batch_classify.py
  python batch_classify.py --batch-size 30 --limit 3   # Test: only 3 batches
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from lib.config import LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT

BATCH_SIZE = 30
DELAY = 0.5  # seconds between batches


def _call_llm(messages, api_key, model, base_url):
    """Call OpenAI-compatible chat API."""
    url = f'{base_url}/chat/completions'
    body = {
        'model': model,
        'messages': messages,
        'temperature': 0.1,
        'max_tokens': 2048,
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'Bearer {api_key}')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result['choices'][0]['message']['content']
    except Exception as e:
        print(f'  API error: {e}', file=sys.stderr)
        return None


def classify_batch(terms, api_key, model, base_url):
    """Classify a batch of terms. Returns list of {term, classification}."""
    term_list = '\n'.join(f'- {t["term"]} (出现{t["freq"]}次)' for t in terms)

    system_prompt = (
        '你是中文专有名词分类专家。判断以下词是"proper_noun"还是"common_word"。\n'
        '专有名词(proper_noun)：人名、地名、组织名、机器人名、星球名、特定角色名、作品标题名\n'
        '普通词(common_word)：动词、形容词、副词、日常用语、感叹词、数量词\n'
        '注意：动画角色名、特定称谓算专名；口语表达、惯用短语算普通词。\n'
        '返回严格JSON数组，每个元素为{"term":"词","classification":"proper_noun或common_word"}'
    )

    user_prompt = f'请分类以下词语：\n\n{term_list}'

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ]

    response = _call_llm(messages, api_key, model, base_url)
    if not response:
        return None

    # Parse JSON response
    import re
    # Strip markdown fences
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```[\w-]*\s*', '', cleaned)
        cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting JSON array from response
    m = re.search(r'\[.*\]', response, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    print(f'  Failed to parse response: {response[:300]}', file=sys.stderr)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--limit', type=int, default=0, help='Only process N batches')
    parser.add_argument('--candidates', default='temp/scans/candidates.json')
    parser.add_argument('--output', default='temp/scans/classified_terms.json')
    args = parser.parse_args()

    api_key = LLM_API_KEY
    model = LLM_MODEL or LLM_MODEL_DEFAULT
    base_url = LLM_BASE_URL or LLM_BASE_URL_DEFAULT

    if not api_key:
        print('ERROR: LLM_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    # Load candidates
    with open(args.candidates, 'r', encoding='utf-8') as f:
        data = json.load(f)

    candidates = data.get('candidates', data) if isinstance(data, dict) else data
    unknowns = [c for c in candidates if c.get('type') == 'unknown_suspect']

    # Deduplicate by zh_term, keep highest frequency
    terms_map = {}
    for c in unknowns:
        term = c.get('zh_term', '')
        freq = c.get('frequency', 0)
        if term and (term not in terms_map or freq > terms_map[term]['freq']):
            terms_map[term] = {'term': term, 'freq': freq}

    unique_terms = sorted(terms_map.values(), key=lambda x: -x['freq'])
    print(f'Unique terms to classify: {len(unique_terms)}')

    # Batch
    all_results = {'proper_nouns': [], 'common_words': []}
    batch_count = 0

    for i in range(0, len(unique_terms), args.batch_size):
        batch = unique_terms[i:i + args.batch_size]
        batch_count += 1
        batch_no = batch_count

        if args.limit > 0 and batch_no > args.limit:
            break

        print(f'Batch {batch_no}/{(len(unique_terms) + args.batch_size - 1) // args.batch_size}: '
              f'{len(batch)} terms...', end=' ', flush=True)

        result = classify_batch(batch, api_key, model, base_url)

        if result:
            proper = [r['term'] for r in result if r.get('classification') == 'proper_noun']
            common = [r['term'] for r in result if r.get('classification') == 'common_word']
            all_results['proper_nouns'].extend(proper)
            all_results['common_words'].extend(common)
            print(f'{len(proper)} proper, {len(common)} common')
        else:
            # On failure, mark all as unknown
            print('FAILED — marking all as common (review needed)')
            all_results['common_words'].extend(t['term'] for t in batch)

        time.sleep(DELAY)

    # Deduplicate and sort
    all_results['proper_nouns'] = sorted(set(all_results['proper_nouns']))
    all_results['common_words'] = sorted(set(all_results['common_words']))

    # Save classified terms
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f'\nDone: {len(all_results["proper_nouns"])} proper nouns, '
          f'{len(all_results["common_words"])} common words')
    print(f'Results saved to {args.output}')

    # Write blacklist
    blacklist_path = 'temp/zh_common_blacklist.json'
    existing_blacklist = []
    if os.path.exists(blacklist_path):
        with open(blacklist_path, 'r', encoding='utf-8') as f:
            existing_blacklist = json.load(f)

    new_blacklist = sorted(set(existing_blacklist + all_results['common_words']))
    with open(blacklist_path, 'w', encoding='utf-8') as f:
        json.dump(new_blacklist, f, ensure_ascii=False, indent=2)
    print(f'Blacklist: {len(new_blacklist)} entries → {blacklist_path}')

    # Write noun mappings (self-mapping for zh-only mode)
    mappings_path = 'temp/noun_mappings.json'
    existing_mappings = {}
    if os.path.exists(mappings_path):
        with open(mappings_path, 'r', encoding='utf-8') as f:
            existing_mappings = json.load(f)

    for term in all_results['proper_nouns']:
        if term not in existing_mappings:
            existing_mappings[term] = term  # self-mapping

    os.makedirs(os.path.dirname(mappings_path), exist_ok=True)
    with open(mappings_path, 'w', encoding='utf-8') as f:
        json.dump(existing_mappings, f, ensure_ascii=False, indent=2)
    print(f'Mappings: {len(existing_mappings)} entries → {mappings_path}')


if __name__ == '__main__':
    main()
