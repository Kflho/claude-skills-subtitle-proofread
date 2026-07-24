#!/usr/bin/env python3
"""Unified subprocess helpers — single entry point for all external tool calls.

Eliminates the "subprocess spaghetti" where ffmpeg/whisper/git calls were
scattered across 7+ files with inconsistent encoding, timeout, and error
handling.

Usage:
  from lib.subprocess_utils import run_ffmpeg, run_whisper, run_git, SubprocessError

  # ffmpeg / ffprobe
  run_ffmpeg(['-i', video, '-vn', '-ac', '1', '-ar', '16000', out])
  result = run_ffmpeg(['-i', audio, '-af', 'silencedetect=...', '-f', 'null', '-'],
                       tool='ffmpeg')  # stderr used by silencedetect

  # ffprobe
  result = run_ffmpeg(['-v', 'quiet', '-show_entries', 'format=duration',
                       '-of', 'csv=p=0', audio], tool='ffprobe')

  # whisper.cpp
  result = run_whisper(['-m', model, '-l', 'ja', '-f', audio])

  # git
  run_git(['add', '-A'], cwd=project_dir)
  run_git(['commit', '-m', message], cwd=project_dir)
"""

import os
import subprocess
import sys

from lib.config import PYTHONIOENCODING


# ═══════════════════════════════════════════════════════════════
# SubprocessError
# ═══════════════════════════════════════════════════════════════

class SubprocessError(Exception):
    """Unified exception for all external tool failures.

    Carries enough context that callers can decide retry / fallback /
    escalate without parsing raw subprocess exceptions.
    """

    def __init__(self, tool: str, args: list, returncode: int,
                 stderr: str = '', stdout: str = ''):
        self.tool = tool          # 'ffmpeg' | 'ffprobe' | 'whisper' | 'git'
        self.args = args
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        super().__init__(
            f'{tool} exited with code {returncode}: '
            f'{stderr[-300:] if stderr else "(no stderr)"}'
        )


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════

def _base_env():
    """Environment dict with UTF-8 encoding for subprocess I/O on Windows."""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = PYTHONIOENCODING
    return env


def _run(tool: str, cmd: list, *, timeout: int = 300,
         check: bool = True, cwd: str = None, env: dict = None,
         **kwargs) -> subprocess.CompletedProcess:
    """Core runner — all tool-specific functions delegate here.

    Args:
        tool: human-readable tool name for error messages
        cmd: full command line including the tool binary
        timeout: seconds before SubprocessError
        check: if True, non-zero exit raises SubprocessError
        cwd: working directory
        env: extra env vars (merged on top of UTF-8 base)
        **kwargs: passed through to subprocess.run

    Returns:
        subprocess.CompletedProcess (check=False paths may have non-zero returncode)

    Raises:
        SubprocessError: on timeout, FileNotFoundError, or non-zero exit (if check=True)
    """
    merged_env = _base_env()
    if env:
        merged_env.update(env)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            env=merged_env,
            timeout=timeout,
            cwd=cwd,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        raise SubprocessError(tool, cmd, -1,
                              stderr=f'timeout after {timeout}s')
    except FileNotFoundError:
        raise SubprocessError(tool, cmd, -2,
                              stderr=f'{tool} binary not found: {cmd[0]}')

    if check and result.returncode != 0:
        raise SubprocessError(
            tool, cmd, result.returncode,
            stderr=(result.stderr or '').strip(),
            stdout=(result.stdout or '').strip(),
        )

    return result


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def run_ffmpeg(args: list, *, tool: str = 'ffmpeg',
               timeout: int = 300, check: bool = True,
               cwd: str = None, **kwargs) -> subprocess.CompletedProcess:
    """Run ffmpeg or ffprobe with unified encoding and error handling.

    Args:
        args: command-line args AFTER the tool name (e.g. ['-i', 'in.mp4', 'out.wav'])
        tool: 'ffmpeg' (default) or 'ffprobe'
        timeout: seconds (default 300)
        check: if True, non-zero exit raises SubprocessError
        cwd: working directory
        **kwargs: passed to subprocess.run (e.g. text=True)

    Returns:
        subprocess.CompletedProcess with .stdout, .stderr, .returncode

    Raises:
        SubprocessError: on timeout, binary-not-found, or non-zero exit
    """
    return _run(tool, [tool] + args, timeout=timeout,
                check=check, cwd=cwd, **kwargs)


def run_whisper(args: list, *, tool: str = 'whisper-cli',
                timeout: int = 900, check: bool = True,
                cwd: str = None, **kwargs) -> subprocess.CompletedProcess:
    """Run whisper.cpp CLI with unified encoding and error handling.

    Args:
        args: command-line args AFTER the tool name
        tool: 'whisper-cli' (default) — pass the full path to whisper-cli binary
        timeout: seconds (default 900 — Tier 2 can be long)
        check: if True, non-zero exit raises SubprocessError
        cwd: working directory
        **kwargs: passed to subprocess.run

    Returns:
        subprocess.CompletedProcess

    Raises:
        SubprocessError: on timeout, binary-not-found, or non-zero exit
    """
    return _run('whisper', [tool] + args, timeout=timeout,
                check=check, cwd=cwd, **kwargs)


def run_git(args: list, *, timeout: int = 30, check: bool = True,
            cwd: str = None, **kwargs) -> subprocess.CompletedProcess:
    """Run git with unified encoding and error handling.

    Args:
        args: command-line args AFTER 'git' (e.g. ['add', '-A'])
        timeout: seconds (default 30)
        check: if True, non-zero exit raises SubprocessError
        cwd: working directory (REQUIRED for most git operations)
        **kwargs: passed to subprocess.run

    Returns:
        subprocess.CompletedProcess

    Raises:
        SubprocessError: on timeout, binary-not-found, or non-zero exit
    """
    return _run('git', ['git'] + args, timeout=timeout,
                check=check, cwd=cwd, **kwargs)
