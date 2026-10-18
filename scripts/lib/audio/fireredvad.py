#!/usr/bin/env python3
"""FireRedVAD/AED provider — runs the model in its isolated venv.

The main skill process has no torch; FireRedVAD lives in ``.venv-qwen``.
This provider shells out to ``scripts/audio/firered_worker.py``, which
imports ``fireredvad`` and emits the shared ``AudioHints`` JSON contract.

Usage:
    from lib.audio.fireredvad import FireRedVadProvider

    hints = FireRedVadProvider().detect('episode.wav', mode='aed')
    print(hints.summary())
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from lib.audio.base import AudioHints, AudioProviderError


class FireRedVadProvider:
    """Run FireRedVAD (speech) or AED (speech/singing/music) on a wav."""

    name = 'fireredvad'

    def __init__(self, *, python: str | None = None,
                 models_dir: str | None = None,
                 worker: str | None = None,
                 timeout: float = 1800.0,
                 use_gpu: bool = False,
                 runner=None):
        root = Path(__file__).resolve().parents[3]
        self.root = root
        self.python = Path(python) if python else (
            root / '.venv-qwen' / 'Scripts' / 'python.exe')
        self.models_dir = Path(models_dir) if models_dir else (
            root / 'models' / 'FireRedVAD')
        self.worker = Path(worker) if worker else (
            root / 'scripts' / 'audio' / 'firered_worker.py')
        self.timeout = timeout
        self.use_gpu = use_gpu
        self._runner = runner or self._run

    def available(self, mode: str = 'aed') -> bool:
        model = self.models_dir / ('AED' if mode == 'aed' else 'VAD')
        return (self.python.exists() and self.worker.exists()
                and (model / 'model.pth.tar').exists())

    def _run(self, cmd, timeout):
        return subprocess.run(
            cmd, cwd=str(self.root), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout)

    def detect(self, wav: str, mode: str = 'aed') -> AudioHints:
        if mode not in ('vad', 'aed'):
            raise ValueError(f'unsupported mode: {mode!r} (vad|aed)')
        if not Path(wav).exists():
            raise AudioProviderError(f'audio not found: {wav}')
        if not self.available(mode):
            raise AudioProviderError(
                'FireRedVAD is not available; expected '
                f'{self.python} and {self.models_dir}/'
                f'{"AED" if mode == "aed" else "VAD"}/model.pth.tar')

        fd, out_path = tempfile.mkstemp(prefix='fireredvad_', suffix='.json')
        os.close(fd)
        try:
            cmd = [str(self.python), str(self.worker),
                   '--wav', str(wav),
                   '--models', str(self.models_dir),
                   '--mode', mode,
                   '--out', out_path]
            if self.use_gpu:
                cmd.append('--gpu')
            proc = self._runner(cmd, self.timeout)
            stderr = (getattr(proc, 'stderr', '') or '').strip()
            if getattr(proc, 'returncode', 0) != 0:
                raise AudioProviderError(
                    f'FireRedVAD worker failed (exit {proc.returncode}): '
                    f'{stderr[-500:] or "no stderr"}')
            try:
                with open(out_path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise AudioProviderError(
                    f'FireRedVAD worker returned invalid JSON: {exc}') from exc
            if data.get('error'):
                raise AudioProviderError(str(data['error']))
            hints = AudioHints.from_dict(data)
            hints.meta.setdefault('mode', mode)
            return hints
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
