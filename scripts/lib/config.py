#!/usr/bin/env python3
"""Single source of truth for all configuration.

Import this module instead of reading os.environ directly.
All env var reads happen once at import time.

Usage:
    from lib.config import WHISPER_CLI, LLM_API_KEY
    from lib import config
    model = config.LLM_MODEL or config.LLM_MODEL_DEFAULT
"""

import os

# ═══════════════════════════════════════════════════════════════
# Whisper / ASR
# ═══════════════════════════════════════════════════════════════

WHISPER_CLI        = os.environ.get('WHISPER_CLI', '')
WHISPER_MODEL       = os.environ.get('WHISPER_MODEL', '')
WHISPER_RETRY_MODEL = os.environ.get('WHISPER_RETRY_MODEL', '')
WHISPER_BACKEND     = os.environ.get('WHISPER_BACKEND', '').strip()

# ═══════════════════════════════════════════════════════════════
# LLM API (LLM_* 优先，POLISH_* 为旧名向后兼容)
# ═══════════════════════════════════════════════════════════════

LLM_API_KEY   = os.environ.get('LLM_API_KEY', '') or os.environ.get('POLISH_API_KEY', '')
LLM_MODEL     = os.environ.get('LLM_MODEL', '') or os.environ.get('POLISH_MODEL', '')
LLM_BASE_URL  = os.environ.get('LLM_BASE_URL', '') or os.environ.get('POLISH_BASE_URL', '')

# Hardcoded defaults for LLM (used when env var is empty and CLI arg not given)
LLM_MODEL_DEFAULT    = 'deepseek-flash'
LLM_BASE_URL_DEFAULT = 'https://api.deepseek.com/v1'

# ⚠️ 推理模型的思考 token 会吃光 max_tokens 预算。
# deepseek-flash / deepseek-v4-pro 都返回 reasoning_content，且 completion_tokens
# 里 reasoning_tokens 可占满全部额度 → content 为空串、finish_reason='length'。
# 调用方拿到空串 → 判失败 → 整批 cue 保留原文（**静默**，无任何错误日志）。
# 实测（10 条 cue 一批）：思考开 21.4s / content=0 / 解析 0 条；
#                        思考关  1.4s / content=180 / 解析 10 条。
# 所以默认关闭思考。显式提高 max_tokens 治标不治本——思考长度会跟着涨
# （max_tokens=32768 时 reasoning 13832 tok，62s 才收敛）。
#
# 非 DeepSeek 端点若不认 reasoning_effort，把它设为空串即可省略该参数。
LLM_MAX_TOKENS       = int(os.environ.get('LLM_MAX_TOKENS', '') or 8192)
LLM_REASONING_EFFORT = os.environ.get('LLM_REASONING_EFFORT', 'none')


def apply_llm_params(body, max_tokens=None):
    """给自建 LLM 请求体补上推理模型所需的参数。

    所有自己拼请求体的脚本都该走这里（`lib/llm.py:call_chat` 已内建）。
    漏掉的话，推理模型的思考 token 会吃光 max_tokens 预算，返回空 content，
    调用方静默判失败——不报错、不告警，只是翻译没生效。

    Args:
        body: 已含 model/messages/temperature 的请求体 dict（原地修改）。
        max_tokens: 覆盖默认上限；None 用 LLM_MAX_TOKENS。
    """
    body['max_tokens'] = max_tokens or LLM_MAX_TOKENS
    if LLM_REASONING_EFFORT:
        body['reasoning_effort'] = LLM_REASONING_EFFORT
    return body

# ═══════════════════════════════════════════════════════════════
# Baidu Translate
# ═══════════════════════════════════════════════════════════════

BAIDU_APPID        = os.environ.get('BAIDU_APPID', '')
BAIDU_SECRET       = os.environ.get('BAIDU_SECRET', '')
BAIDU_API_ENDPOINT = os.environ.get('BAIDU_API_ENDPOINT', '') or 'https://fanyi-api.baidu.com/api/trans/vip/translate'

# ═══════════════════════════════════════════════════════════════
# I/O encoding
# ═══════════════════════════════════════════════════════════════

PYTHONIOENCODING = 'utf-8'

# ═══════════════════════════════════════════════════════════════
# Path conventions
# ═══════════════════════════════════════════════════════════════

DEFAULT_INPUT_DIR = 'AI审查后'
VIDEO_CANDIDATES  = ('video', 'videos')

# ═══════════════════════════════════════════════════════════════
# Pipeline defaults
# ═══════════════════════════════════════════════════════════════

DEFAULT_TARGET_LANG       = 'ja'
DEFAULT_TIMEOUT           = 600
DEFAULT_COMPARE_THRESHOLD = 0.4
DEFAULT_MAX_PAD           = 2.0
DEFAULT_MAX_CHARS         = 200


# ═══════════════════════════════════════════════════════════════
# Dynamic (runtime-mutable)
# ═══════════════════════════════════════════════════════════════

def get_input_dir():
    """Return the current subtitle input directory.

    Reads env vars at call time (not import time) because run_all.py
    may set INPUT_DIR after import via os.environ['INPUT_DIR'] = ....

    Precedence: SUBTITLE_INPUT_DIR > INPUT_DIR > DEFAULT_INPUT_DIR.
    """
    return (os.environ.get('SUBTITLE_INPUT_DIR', '')
            or os.environ.get('INPUT_DIR', '')
            or DEFAULT_INPUT_DIR)
