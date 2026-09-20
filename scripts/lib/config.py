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
# VAD / 对白-人声匹配
# ═══════════════════════════════════════════════════════════════
#
# ⚠️ 这些阈值只用于「提示」，不是判据。本作（对白垫 BGM）实测：Silero 硬过滤
# 会丢掉大量真实台词（单集 243/463），webrtcvad 又把音乐判成语音。任何一处
# 拿它们当硬门（删条、定 region、定时间码）之前，先想清楚误杀怎么办。

# WebRTC VAD
VAD_FRAME_MS          = 30      # 帧长；VAD 只接受 10/20/30ms
VAD_AGGRESSIVENESS    = 2       # 0=最宽松 3=最激进
VAD_MIN_SPEECH_S      = 0.3     # 单段最短语音
VAD_MERGE_GAP_S       = 0.5     # 相邻语音段合并间隔

# silencedetect 回退（区分不了语音/音乐，只作兜底）
SILENCE_DB            = -30
SILENCE_MIN_S         = 0.8
SILENCE_MIN_SPEECH_S  = 0.3
SILENCE_PADDING_S     = 0.15

# 判定「这条 cue 有没有人声」的三套口径 —— 刻意不同，改之前先读调用点：
VAD_OVERLAP_DELETE_S  = 0.0     # 删条用：相接即算有人声（最保守，宁可不删）
VAD_OVERLAP_REGION_S  = 0.3     # 建修复区用
VAD_FRAGMENT_WINDOW_S = 5.0     # fragment 升级用：cue 起点后固定窗口

# 删条/建区的时长与节奏阈值
VAD_DELETE_MAX_DUR_S  = 3.0     # 超过这个时长，即使无人声重叠也保留
FIX_REGION_MIN_GAP_S  = 3.0     # 语音超出 cue 覆盖多少才算 partial_overlap
FIX_REGION_MAX_GAP_S  = 45.0    # 超过这个时长的语音段跳过（OP/ED 歌）
GAP_SEC               = 5.0     # 无上下文 cue 时，修复区向两侧扩多少

# ⚠️ OP/ED 边界：曾经按「OP 最长 180s」写死，而本作 OP 只有 82s —— 预替换
# 的窗口一路吃进正片，单集 45 条对白被歌词覆写且不报警。**不要**再用固定
# 时间窗判定 OP/ED；用 oped_detect 的相似度/重复度信号。
OP_BOUNDARY_SEC       = 180
ED_BOUNDARY_SEC       = 180

# Tier 2 采纳 Whisper 转录的门槛 —— 口径是「该 cue 的时长被最佳 whisper 段
# 覆盖了多大比例」，与上面三套「cue 有没有人声」的口径无关，别混用。
WHISPER_REPLACE_MIN_COVERAGE  = 0.3   # 低于此比例不采纳
WHISPER_REPLACE_HIGH_COVERAGE = 0.5   # 达到此比例标 confidence=high


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
