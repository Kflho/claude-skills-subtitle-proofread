"""Tests for lib/subprocess_utils.py — SubprocessError + mock-based tool tests."""

import subprocess
from unittest.mock import patch, MagicMock
import pytest
from lib.subprocess_utils import (
    SubprocessError, _base_env,
    run_ffmpeg, run_whisper, run_git,
)


class TestSubprocessError:
    def test_full_constructor(self):
        err = SubprocessError('ffmpeg', ['-i', 'x.mp4'], 1,
                              stderr='error msg', stdout='some output')
        assert err.tool == 'ffmpeg'
        assert err.returncode == 1
        assert 'ffmpeg' in str(err)
        assert 'error msg' in str(err)

    def test_minimal_constructor(self):
        err = SubprocessError('git', ['push'], 128)
        assert err.tool == 'git'
        assert err.returncode == 128
        assert '(no stderr)' in str(err)

    def test_stdout_without_stderr(self):
        err = SubprocessError('whisper-cli', ['-m', 'model.bin'], 1,
                              stdout='output only')
        # str(err) includes tool name and return code
        assert 'whisper-cli' in str(err)
        assert '1' in str(err) or 'output only' in str(err)


class TestBaseEnv:
    def test_utf8_encoding_set(self):
        env = _base_env()
        assert env['PYTHONIOENCODING'] == 'utf-8'

    def test_inherits_os_environ(self):
        env = _base_env()
        assert 'PATH' in env or 'SystemRoot' in env
        assert isinstance(env, dict)


class TestRunFfmpegMock:
    def test_success(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = b'output'
            mock_result.stderr = b''
            mock_run.return_value = mock_result

            result = run_ffmpeg(['-i', 'in.mp4', 'out.wav'])
            assert result.returncode == 0

    def test_timeout_error(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['ffmpeg'], timeout=5)

            with pytest.raises(SubprocessError) as exc_info:
                run_ffmpeg(['-i', 'in.mp4', 'out.wav'], timeout=5)
            assert 'timeout' in str(exc_info.value).lower()

    def test_nonzero_exit_with_check(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = b'file not found'
            mock_result.stdout = b''
            mock_run.return_value = mock_result

            with pytest.raises(SubprocessError):
                run_ffmpeg(['-i', 'missing.mp4', 'out.wav'], check=True)


class TestRunWhisperMock:
    def test_success(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = b'[00:00:01.000 --> 00:00:05.000] text'
            mock_run.return_value = mock_result

            result = run_whisper(['-m', 'model.bin', '-f', 'audio.wav'])
            assert result.returncode == 0


class TestRunGitMock:
    def test_success(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            result = run_git(['status'])
            assert result.returncode == 0

    def test_nonzero(self):
        with patch('lib.subprocess_utils.subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 128
            mock_result.stderr = b'not a git repository'
            mock_result.stdout = b''
            mock_run.return_value = mock_result

            with pytest.raises(SubprocessError):
                run_git(['log'], check=True)
