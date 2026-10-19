#!/usr/bin/env python3
"""Qwen3-ASR worker client — spawns the isolated worker and speaks JSON lines.

The main skill environment does not import torch/qwen-asr.  This client
starts `scripts/asr/qwen3_worker.py` with a dedicated Python executable
(usually `.venv-qwen\\Scripts\\python.exe`), keeps it warm, and sends one
request per line.

Usage:
    from lib.asr.qwen3 import Qwen3WorkerClient
    with Qwen3WorkerClient(python_exe=r'.venv-qwen\\Scripts\\python.exe') as c:
        result = c.transcribe('ep.wav', language='Japanese', timestamps=True)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import importlib.util
from collections import deque
from pathlib import Path

from lib.asr.base import (
    ASRConfig, ASRBackend, ASRError, ASRResult, AlignResult, BackendInfo,
)
from lib.config import ASR_DEVICE, ASR_MODEL, ALIGNER_MODEL, QWEN_PYTHON


NAME = 'qwen3'
KIND = 'asr'
INFO = BackendInfo(
    name=NAME,
    kind=KIND,
    version='0.0.6',
    capabilities=('timestamps', 'context'),
    auto_priority=10,
    description='Qwen3-ASR via isolated worker; token timestamps via ForcedAligner.',
)


def default_worker_script() -> Path:
    """scripts/asr/qwen3_worker.py relative to this file."""
    return Path(__file__).resolve().parents[3] / 'asr' / 'qwen3_worker.py'


class Qwen3WorkerClient:
    """Long-lived JSON-lines client for the Qwen3 worker."""

    def __init__(self, *, python_exe: str | None = None,
                 worker_script: str | Path | None = None,
                 asr_model: str | None = None,
                 aligner_model: str | None = None,
                 device: str | None = None,
                 dtype: str = 'bfloat16',
                 max_new_tokens: int = 4096,
                 attn_implementation: str = '',
                 project_root: str | Path | None = None,
                 startup_timeout: float = 60.0,
                 request_timeout: float = 1800.0,
                 extra_args: list[str] | None = None):
        self.python_exe = python_exe or QWEN_PYTHON or sys.executable
        self.worker_script = Path(worker_script) if worker_script else default_worker_script()
        self.asr_model = asr_model or ASR_MODEL
        self.aligner_model = aligner_model if aligner_model is not None else ALIGNER_MODEL
        self.device = device or ASR_DEVICE
        self.dtype = dtype
        self.max_new_tokens = max_new_tokens
        self.attn_implementation = attn_implementation
        self.project_root = Path(project_root) if project_root else self.worker_script.parents[2]
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.extra_args = list(extra_args or [])

        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stdout_q: queue.Queue = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._next_id = 0
        self._lock = threading.Lock()

    # ── lifecycle ──────────────────────────────────────────────

    def available(self) -> bool:
        """Cheap check: interpreter/worker exist; current env has qwen_asr."""
        if not (Path(self.python_exe).exists()
                and Path(self.worker_script).exists()):
            return False
        try:
            same_interp = (Path(self.python_exe).resolve()
                           == Path(sys.executable).resolve())
        except OSError:
            same_interp = False
        if same_interp:
            return importlib.util.find_spec('qwen_asr') is not None
        return True

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if not Path(self.python_exe).exists():
            raise ASRError(f'Qwen3 python not found: {self.python_exe}')
        if not Path(self.worker_script).exists():
            raise ASRError(f'Qwen3 worker not found: {self.worker_script}')

        cmd = [
            str(self.python_exe), str(self.worker_script),
            '--asr', str(self.asr_model),
            '--aligner', str(self.aligner_model or ''),
            '--device', str(self.device),
            '--dtype', self.dtype,
            '--max-new-tokens', str(self.max_new_tokens),
        ]
        if self.attn_implementation:
            cmd.extend(['--attn-implementation', self.attn_implementation])
        cmd.extend(self.extra_args)

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'

        self._stdout_q = queue.Queue()
        self._stderr_tail.clear()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            cwd=str(self.project_root),
            env=env,
        )
        self._reader_thread = threading.Thread(
            target=self._read_stdout, name='qwen3-stdout', daemon=True)
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name='qwen3-stderr', daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()

        # ping is cheap (models load lazily); verifies the protocol early.
        try:
            self.request({'cmd': 'ping'}, timeout=self.startup_timeout)
        except Exception:
            tail = self.stderr_tail()
            self.close()
            raise ASRError(
                f'Qwen3 worker failed to start (cmd={cmd[0]}); '
                f'stderr tail:\n{tail}'
            )

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(
                        json.dumps({'cmd': 'shutdown'}) + '\n')
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
        finally:
            self._proc = None
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass

    def __enter__(self) -> 'Qwen3WorkerClient':
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── protocol ──────────────────────────────────────────────

    def stderr_tail(self) -> str:
        return '\n'.join(self._stderr_tail)

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._stdout_q.put(line)
        self._stdout_q.put(None)  # EOF sentinel

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip('\n')
            if not line:
                continue
            self._stderr_tail.append(line)
            print(line, file=sys.stderr)

    def _write_line(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ASRError('Qwen3 worker is not running')
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
        proc.stdin.flush()

    def request(self, payload: dict, *, timeout: float | None = None) -> dict:
        """Send one request and wait for its response."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise ASRError(
                    'Qwen3 worker is not running; call start() first. '
                    f'stderr tail:\n{self.stderr_tail()}'
                )
            self._next_id += 1
            req_id = payload.get('id') or f'r{self._next_id}'
            payload = dict(payload)
            payload['id'] = req_id
            self._write_line(payload)

            deadline = time.time() + (timeout or self.request_timeout)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise ASRError(
                        f'Qwen3 worker timeout after {timeout or self.request_timeout}s '
                        f'(cmd={payload.get("cmd")!r}, id={req_id!r})'
                    )
                try:
                    line = self._stdout_q.get(timeout=remaining)
                except queue.Empty:
                    continue
                if line is None:
                    code = self._proc.poll() if self._proc else None
                    raise ASRError(
                        f'Qwen3 worker exited (code={code}) during '
                        f'{payload.get("cmd")!r}. stderr tail:\n{self.stderr_tail()}'
                    )
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Some libraries print progress to stdout. Keep stderr
                    # clean-ish and do not crash the protocol.
                    print(f'[qwen3] non-JSON stdout: {line[:300]}', file=sys.stderr)
                    continue
                if obj.get('id') not in (None, req_id):
                    # A late response for another request; ignore it.
                    continue
                if not obj.get('ok', False):
                    raise ASRError(
                        f'Qwen3 {payload.get("cmd")!r} failed: '
                        f'{obj.get("error", "unknown error")}\n'
                        f'{obj.get("traceback", "")}'
                    )
                return obj.get('result', {})

    # ── high-level API ────────────────────────────────────────

    def ping(self, timeout: float = 10.0) -> dict:
        return self.request({'cmd': 'ping'}, timeout=timeout)

    def load(self, timeout: float | None = None) -> dict:
        """Load models now; used by batch warmup to exclude load time."""
        return self.request({'cmd': 'load'},
                            timeout=timeout or self.request_timeout)

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = True,
                   chunk_sec: float | None = None,
                   timeout: float | None = None) -> ASRResult:
        payload = {
            'cmd': 'transcribe',
            'audio': str(audio_path),
            'language': language,
            'context': context,
            'timestamps': bool(timestamps),
        }
        if chunk_sec is not None and float(chunk_sec) > 0:
            payload['chunk_sec'] = float(chunk_sec)
        result = self.request(payload, timeout=timeout)
        out = ASRResult.from_dict(result)
        if not out.engine:
            out.engine = 'qwen3'
        return out

    def align(self, audio_path: str, text: str, *, language: str = 'ja',
              timeout: float | None = None) -> AlignResult:
        result = self.request({
            'cmd': 'align',
            'audio': str(audio_path),
            'text': text,
            'language': language,
        }, timeout=timeout)
        return AlignResult.from_dict(result)


class Qwen3ASRBackend:
    """ASRBackend wrapper around Qwen3WorkerClient."""

    name = 'qwen3'

    def __init__(self, client: Qwen3WorkerClient | None = None,
                 chunk_sec: float = 0.0, **kwargs):
        self.client = client or Qwen3WorkerClient(**kwargs)
        self.chunk_sec = float(chunk_sec or 0.0)

    def available(self) -> bool:
        return self.client.available()

    def warmup(self) -> None:
        """Load the model now so batch timing excludes startup."""
        if self.client._proc is None or self.client._proc.poll() is not None:
            self.client.start()
        self.client.load()

    def transcribe(self, audio_path: str, *, language: str = 'ja',
                   context: str = '', timestamps: bool = True) -> ASRResult:
        self.warmup()
        return self.client.transcribe(
            audio_path, language=language, context=context,
            timestamps=timestamps, chunk_sec=self.chunk_sec or None)

    def align(self, audio_path: str, text: str, *,
              language: str = 'ja') -> AlignResult:
        """Forced-alignment capability (available when worker has aligner)."""
        self.warmup()
        return self.client.align(audio_path, text, language=language)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> 'Qwen3ASRBackend':
        self.client.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def build(config: ASRConfig, **kwargs) -> Qwen3ASRBackend:
    """Registry factory: the only Qwen3-specific glue the core calls."""
    return Qwen3ASRBackend(
        python_exe=kwargs.get('python_exe') or config.python or QWEN_PYTHON or None,
        asr_model=kwargs.get('asr_model') or config.model or ASR_MODEL,
        aligner_model=(kwargs.get('aligner_model')
                       or config.aligner_model or ALIGNER_MODEL),
        device=kwargs.get('device') or config.device or ASR_DEVICE,
        dtype=kwargs.get('dtype') or config.dtype,
        max_new_tokens=(kwargs.get('max_new_tokens')
                        or config.max_new_tokens or 4096),
        request_timeout=(kwargs.get('request_timeout')
                         or config.request_timeout or 1800),
        chunk_sec=(kwargs.get('chunk_sec')
                   or config.option('chunk_sec', 0.0)),
        **{k: v for k, v in kwargs.items()
           if k not in {'python_exe', 'asr_model', 'aligner_model',
                        'device', 'dtype', 'max_new_tokens',
                        'request_timeout', 'chunk_sec'}},
    )
