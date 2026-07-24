"""Tests for lib/config.py — env var retrieval and defaults."""

import os
import pytest
from lib.config import (
    get_input_dir, DEFAULT_INPUT_DIR,
    WHISPER_CLI, WHISPER_MODEL, WHISPER_RETRY_MODEL, WHISPER_BACKEND,
    LLM_API_KEY, LLM_MODEL, LLM_BASE_URL,
    LLM_MODEL_DEFAULT, LLM_BASE_URL_DEFAULT,
    BAIDU_APPID, BAIDU_SECRET, BAIDU_API_ENDPOINT,
    PYTHONIOENCODING, VIDEO_CANDIDATES,
    DEFAULT_TARGET_LANG, DEFAULT_TIMEOUT,
    DEFAULT_COMPARE_THRESHOLD, DEFAULT_MAX_PAD, DEFAULT_MAX_CHARS,
)


class TestGetInputDir:
    """Tests for the dynamic get_input_dir() function."""

    def test_default(self, clean_input_dir_env):
        assert get_input_dir() == DEFAULT_INPUT_DIR

    def test_input_dir_env(self, clean_input_dir_env):
        os.environ['INPUT_DIR'] = 'custom_input'
        assert get_input_dir() == 'custom_input'

    def test_subtitle_input_dir_wins(self, clean_input_dir_env):
        os.environ['SUBTITLE_INPUT_DIR'] = 'subtitle_path'
        os.environ['INPUT_DIR'] = 'other_path'
        assert get_input_dir() == 'subtitle_path'

    def test_subtitle_input_dir_alone(self, clean_input_dir_env):
        os.environ['SUBTITLE_INPUT_DIR'] = 'only_sub'
        assert get_input_dir() == 'only_sub'


class TestWhisperConfig:
    """Whisper env var constants — verify they're strings (empty when unset)."""

    def test_whisper_cli_is_str(self):
        assert isinstance(WHISPER_CLI, str)

    def test_whisper_model_is_str(self):
        assert isinstance(WHISPER_MODEL, str)

    def test_whisper_retry_model_is_str(self):
        assert isinstance(WHISPER_RETRY_MODEL, str)

    def test_whisper_backend_is_str(self):
        assert isinstance(WHISPER_BACKEND, str)


class TestLLMConfig:
    """LLM config constants."""

    def test_llm_api_key_is_str(self):
        assert isinstance(LLM_API_KEY, str)

    def test_llm_model_falls_back_to_default(self):
        assert isinstance(LLM_MODEL, str)
        assert isinstance(LLM_MODEL_DEFAULT, str)
        # At least one is non-empty (the default)
        assert bool(LLM_MODEL or LLM_MODEL_DEFAULT)

    def test_llm_base_url_falls_back_to_default(self):
        assert isinstance(LLM_BASE_URL, str)
        assert isinstance(LLM_BASE_URL_DEFAULT, str)
        assert bool(LLM_BASE_URL or LLM_BASE_URL_DEFAULT)


class TestBaiduConfig:
    """Baidu Translate config."""

    def test_baidu_appid_is_str(self):
        assert isinstance(BAIDU_APPID, str)

    def test_baidu_secret_is_str(self):
        assert isinstance(BAIDU_SECRET, str)

    def test_baidu_api_endpoint_has_default(self):
        assert isinstance(BAIDU_API_ENDPOINT, str)
        assert BAIDU_API_ENDPOINT != ''  # has a hardcoded default URL


class TestPathDefaults:
    """Path and default constants."""

    def test_default_input_dir(self):
        assert DEFAULT_INPUT_DIR == 'AI审查后'

    def test_video_candidates(self):
        assert 'video' in VIDEO_CANDIDATES
        assert 'videos' in VIDEO_CANDIDATES

    def test_python_io_encoding(self):
        assert PYTHONIOENCODING == 'utf-8'

    def test_default_target_lang(self):
        assert DEFAULT_TARGET_LANG == 'ja'

    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT == 600

    def test_default_compare_threshold(self):
        assert DEFAULT_COMPARE_THRESHOLD == 0.4

    def test_default_max_pad(self):
        assert DEFAULT_MAX_PAD == 2.0

    def test_default_max_chars(self):
        assert DEFAULT_MAX_CHARS == 200
