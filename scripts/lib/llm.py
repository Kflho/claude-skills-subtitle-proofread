#!/usr/bin/env python3
"""Shared LLM chat client (OpenAI-compatible) + robust JSON extraction.

Consolidates the `_call_llm` pattern duplicated across legacy scripts.
New code (noun extraction / aggregation) should use this module.
Legacy scripts still carry their own copies — migrating them is optional.

Usage:
    from lib.llm import call_chat, extract_json_array

    text = call_chat([{'role': 'system', 'content': '...'},
                      {'role': 'user', 'content': '...'}])
    arr = extract_json_array(text)   # -> list | None
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error

from lib.config import (
    LLM_API_KEY, LLM_MODEL, LLM_BASE_URL,
    LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT,
)


def call_chat(messages, api_key=None, model=None, base_url=None,
              temperature=0.1, max_tokens=4096, timeout=120, retries=2,
              delay=1.0):
    """Call an OpenAI-compatible chat API. Returns content string or None.

    Args:
        messages: list of {'role': ..., 'content': ...} dicts.
        api_key/model/base_url: fall back to lib.config env/defaults.
        retries: number of additional attempts after the first failure.
    """
    key = api_key or LLM_API_KEY
    mdl = model or LLM_MODEL or LLM_MODEL_DEFAULT
    base = (base_url or LLM_BASE_URL or LLM_BASE_URL_DEFAULT).rstrip('/')

    if not key:
        print('[llm] LLM_API_KEY 为空，无法调用', file=sys.stderr)
        return None

    url = f'{base}/chat/completions'
    body = {
        'model': mdl,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
    data = json.dumps(body).encode('utf-8')

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'Bearer {key}')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                return result['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8')[:300]
            except Exception:
                pass
            print(f'  [llm] HTTP {e.code}: {detail}', file=sys.stderr)
        except Exception as e:
            print(f'  [llm] API error: {e}', file=sys.stderr)
        if attempt < retries:
            time.sleep(delay * (attempt + 1))
    return None


def extract_json_array(text):
    """Extract the first JSON array from an LLM response.

    Tolerates markdown fences, surrounding prose, and trailing commas.
    Returns a list, or None if no valid array could be found.
    """
    if not text:
        return None

    # 1. Strip markdown fences
    cleaned = text.strip()
    cleaned = re.sub(r'^```[\w-]*\s*', '', cleaned)
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    # 2. Find the outermost [ ... ] span
    start = cleaned.find('[')
    if start == -1:
        return None
    depth = 0
    end = -1
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None

    snippet = cleaned[start:end + 1]
    # Tolerate trailing commas before } or ]
    snippet = re.sub(r',\s*([}\]])', r'\1', snippet)

    try:
        return json.loads(snippet)
    except json.JSONDecodeError as e:
        print(f'  [llm] JSON array 解析失败: {e}', file=sys.stderr)
        return None
