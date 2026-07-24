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
LLM_MODEL_DEFAULT    = 'deepseek-chat'
LLM_BASE_URL_DEFAULT = 'https://api.deepseek.com/v1'

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
