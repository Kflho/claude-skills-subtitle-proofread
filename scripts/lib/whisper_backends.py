#!/usr/bin/env python3
"""Whisper multi-backend abstraction — unified interface over multiple ASR engines.

Supported backends:
  - whisper-cpp     (whisper.cpp CLI, GGML models)
  - faster-whisper  (CTranslate2, Python API)
  - openai-whisper  (OpenAI Whisper, PyTorch .pt models)

Backend detection follows priority:
  1. WHISPER_BACKEND env var (explicit override)
  2. Check executable/import availability
  3. CLI backend preferred over Python API (no PyTorch dependency)

Unified return format (all backends):
  [{start_s: float, end_s: float, text: str,
    no_speech_prob: float, avg_logprob: float | None,
    compression_ratio: float | None}, ...]

Usage:
  from lib.whisper_backends import detect_available_backends, transcribe, get_backend_info

  # Init wizard: what's available?
  backends = detect_available_backends()
  # → ['whisper-cpp', 'faster-whisper']  or  [] (nothing found)

  # Transcribe:
  segs = transcribe('audio.wav', model_path='.../model.bin',
                    backend='whisper-cpp', language='ja')
"""

import json
import os
import re
import subprocess
import sys


# ═══════════════════════════════════════════════════════════════
# Backend registry
# ═══════════════════════════════════════════════════════════════

BACKEND_INFO = {
    'whisper-cpp': {
        'name': 'whisper.cpp',
        'description': 'GGML 量化模型，GPU 加速 (CUDA/Metal/Vulkan)，内存占用低',
        'model_format': '.bin (GGML)',
        'detect_method': 'executable',
        'install_guide': 'https://github.com/ggerganov/whisper.cpp/releases',
        'model_guide': 'https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml',
        'recommended_model': 'kotoba-whisper-v2.0 q5_0 (日语) 或 large-v3 q5_0 (通用)',
    },
    'faster-whisper': {
        'name': 'faster-whisper',
        'description': 'CTranslate2 引擎，比原版快 4x，内存效率更高',
        'model_format': 'CTranslate2 目录 (model.bin + config.json)',
        'detect_method': 'import',
        'install_guide': 'pip install faster-whisper',
        'model_guide': '模型自动下载 (HuggingFace) 或本地路径',
        'recommended_model': 'kotoba-tech/kotoba-whisper-v2.0 (日语) 或 deepdml/faster-whisper-large-v3-turbo-ct2',
    },
    'openai-whisper': {
        'name': 'openai-whisper',
        'description': 'OpenAI 原版 Whisper，PyTorch 推理',
        'model_format': '.pt (PyTorch)',
        'detect_method': 'import',
        'install_guide': 'pip install openai-whisper',
        'model_guide': '模型自动下载 (~/.cache/whisper/) 或本地路径',
        'recommended_model': 'large-v3 (日语/通用)',
    },
}


def _is_exe(path):
    """Check if a path points to an executable file."""
    if not path or not os.path.isfile(path):
        return False
    return os.access(path, os.X_OK) or path.lower().endswith('.exe')


# ═══════════════════════════════════════════════════════════════
# Backend detection
# ═══════════════════════════════════════════════════════════════

def detect_available_backends():
    """Detect which Whisper backends are available on this system.

    Returns:
        list[str]: backend IDs that are ready to use, e.g. ['whisper-cpp', 'faster-whisper']
                   Empty list if nothing is installed.
    """
    available = []

    # ── 1. Explicit env var ──
    explicit = os.environ.get('WHISPER_BACKEND', '').strip()
    if explicit:
        # Validate that the declared backend actually works
        if explicit in BACKEND_INFO and _check_backend(explicit):
            return [explicit]  # Explicit = only this one, even if others exist
        # Declared but not working → still return it so init wizard can show error
        if explicit in BACKEND_INFO:
            return []

    # ── 2. Auto-detect all ──
    for bid in BACKEND_INFO:
        if _check_backend(bid):
            available.append(bid)

    return available


def _check_backend(backend_id):
    """Check if a specific backend is available. Returns bool."""
    info = BACKEND_INFO.get(backend_id)
    if not info:
        return False

    method = info['detect_method']

    if method == 'executable':
        return _check_whisper_cpp()
    elif method == 'import':
        return _check_python_backend(backend_id)
    return False


def _check_whisper_cpp():
    """Check if whisper.cpp CLI is available."""
    cli_path = os.environ.get('WHISPER_CLI', '')
    if cli_path and _is_exe(cli_path):
        return True
    # Also check common names on PATH
    for name in ['whisper-cli', 'whisper-cli.exe', 'whisper', 'main']:
        try:
            result = subprocess.run([name, '--help'], capture_output=True, timeout=10)
            if result.returncode == 0 or b'whisper' in (result.stdout or b'') + (result.stderr or b''):
                return True
        except Exception:
            continue
    return False


def _check_python_backend(backend_id):
    """Check if a Python-based backend is importable."""
    if backend_id == 'faster-whisper':
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False
    elif backend_id == 'openai-whisper':
        try:
            import whisper  # noqa: F401  (openai-whisper package)
            return True
        except ImportError:
            return False
    return False


def get_backend_info(backend_id):
    """Get human-readable info for a backend."""
    return BACKEND_INFO.get(backend_id, {})


# ═══════════════════════════════════════════════════════════════
# Version detection (whisper.cpp)
# ═══════════════════════════════════════════════════════════════

_WHISPER_CPP_VERSION_CACHE = None


def detect_whisper_cpp_version(whisper_cli=None):
    """Detect whisper.cpp version by running --help.

    Returns:
        tuple: (major, minor, patch) e.g. (1, 7, 2), or None if detection fails.
        Also returns the raw version string for diagnostics.
    """
    global _WHISPER_CPP_VERSION_CACHE
    if _WHISPER_CPP_VERSION_CACHE is not None:
        return _WHISPER_CPP_VERSION_CACHE

    cli = whisper_cli or os.environ.get('WHISPER_CLI', 'whisper-cli')
    try:
        # Try --version first (newer builds), then fall back to --help
        for flag in ['--version', '--help', '-h']:
            result = subprocess.run(
                [cli, flag], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=15)
            output = (result.stdout or '') + (result.stderr or '')

            # Known version patterns:
            #   "whisper.cpp v1.7.2" (--version, newer builds)
            #   "whisper.cpp : 1.7.2" (older)
            #   "version: 1.5.4" (some builds)
            m = re.search(r'(?:whisper\.cpp\s*(?::|v)?|version:\s*)\s*(\d+)\.(\d+)\.(\d+)', output)
            if m:
                version = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                _WHISPER_CPP_VERSION_CACHE = version
                return version

            # Try just "v1.7.2" pattern
            m2 = re.search(r'v?(\d+)\.(\d+)\.(\d+)', output)
            if m2:
                version = (int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                _WHISPER_CPP_VERSION_CACHE = version
                return version

        # Can't determine version
        _WHISPER_CPP_VERSION_CACHE = None
        return None
    except Exception:
        _WHISPER_CPP_VERSION_CACHE = None
        return None


# ═══════════════════════════════════════════════════════════════
# Timecode helper (shared)
# ═══════════════════════════════════════════════════════════════

def _to_seconds(tc):
    """Timecode string → float seconds."""
    tc = tc.replace(',', '.').replace('-', ':')
    parts = tc.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


# ═══════════════════════════════════════════════════════════════
# Unified transcribe interface
# ═══════════════════════════════════════════════════════════════

def transcribe(audio_path, model_path, language='ja', *,
               backend=None,
               threads=8, processors=2,
               beam_size=5, best_of=8,
               nth=0.6, max_context=0,
               no_fallback=False, suppress_nst=False,
               **kwargs):
    """Transcribe audio with the specified backend.

    Args:
        audio_path: 16kHz mono WAV file
        model_path: path to model (GGML .bin, CT2 dir, or whisper model name)
        language: language code (default 'ja')
        backend: 'whisper-cpp' | 'faster-whisper' | 'openai-whisper' | None (auto)
        threads: CPU threads (whisper-cpp only)
        processors: GPU processors (whisper-cpp only)
        beam_size: beam search width
        best_of: best-of candidates
        nth: no-speech threshold 0.0-1.0
        max_context: cross-segment context tokens (0=disabled)
        no_fallback: disable temperature fallback
        suppress_nst: suppress non-speech tokens (⚠️ may cause hallucinations)

    Returns:
        [{start_s, end_s, text, no_speech_prob, avg_logprob, compression_ratio}, ...]
        Empty list on failure.
    """
    # ── Resolve backend ──
    if backend is None:
        available = detect_available_backends()
        if not available:
            print('⚠ No Whisper backend available — install whisper.cpp, '
                  'faster-whisper, or openai-whisper', file=sys.stderr)
            return []
        backend = available[0]  # First available
        print(f'[whisper] Auto-detected backend: {backend}', file=sys.stderr)

    if backend not in BACKEND_INFO:
        print(f'⚠ Unknown backend: {backend}', file=sys.stderr)
        return []

    # ── Validate backend availability ──
    if not _check_backend(backend):
        print(f'⚠ Backend "{backend}" declared but not available on this system',
              file=sys.stderr)
        info = BACKEND_INFO[backend]
        print(f'   Install: {info["install_guide"]}', file=sys.stderr)
        return []

    # ── Dispatch ──
    if backend == 'whisper-cpp':
        # Accept whisper_cli from kwargs (passed by whisper_utils.run_whisper)
        # or fall back to WHISPER_CLI env var, or PATH lookup
        whisper_cli = kwargs.pop('whisper_cli', None) or os.environ.get('WHISPER_CLI', 'whisper-cli')
        return _transcribe_whisper_cpp(
            audio_path, model_path, language,
            whisper_cli=whisper_cli,
            threads=threads, processors=processors,
            beam_size=beam_size, best_of=best_of,
            nth=nth, max_context=max_context,
            no_fallback=no_fallback, suppress_nst=suppress_nst,
        )
    elif backend == 'faster-whisper':
        return _transcribe_faster_whisper(
            audio_path, model_path, language,
            beam_size=beam_size, best_of=best_of,
            nth=nth, no_fallback=no_fallback,
            **kwargs,
        )
    elif backend == 'openai-whisper':
        return _transcribe_openai_whisper(
            audio_path, model_path, language,
            beam_size=beam_size, best_of=best_of,
            nth=nth, no_fallback=no_fallback,
            **kwargs,
        )

    return []


# ═══════════════════════════════════════════════════════════════
# Backend: whisper.cpp CLI
# ═══════════════════════════════════════════════════════════════

def _transcribe_whisper_cpp(audio_path, model_path, language,
                            whisper_cli=None,
                            threads=8, processors=2,
                            beam_size=5, best_of=8,
                            nth=0.6, max_context=0,
                            no_fallback=False, suppress_nst=False):
    """Transcribe via whisper.cpp CLI. Returns unified segment list."""
    cli = whisper_cli or os.environ.get('WHISPER_CLI', 'whisper-cli')

    cmd = [
        cli, '-m', model_path, '-f', audio_path, '-l', language,
        '-t', str(threads), '-p', str(processors),
        '-bs', str(beam_size), '-bo', str(best_of),
        '-oj', '-of', audio_path + '.whisper', '--print-progress',
        '-nth', str(nth),
        '-mc', str(max_context),
    ]
    if suppress_nst:
        cmd.append('-sns')
    if no_fallback:
        cmd.append('-nf')

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=1800)

    # Print stderr for diagnostics
    for line in (proc.stderr or '').strip().split('\n'):
        if line.strip():
            print(f'  [whisper] {line.strip()}', file=sys.stderr)

    # Check for non-zero exit
    if proc.returncode != 0:
        print(f'⚠ whisper-cli exited with code {proc.returncode}',
              file=sys.stderr)
        if proc.stderr:
            print(f'   stderr: {proc.stderr.strip()[-500:]}', file=sys.stderr)

    json_path = audio_path + '.whisper.json'
    if not os.path.exists(json_path):
        print('⚠ whisper-cli 未生成 JSON 输出', file=sys.stderr)
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print('⚠ whisper JSON 解码失败', file=sys.stderr)
        if os.path.exists(json_path):
            os.remove(json_path)
        return []

    os.remove(json_path)

    # ── Parse JSON with version-aware compat ──
    # whisper.cpp v1.6.0+ → 'transcription'
    # whisper.cpp v1.5.x  → 'segments' (legacy)
    # Use .get() chain: prefer 'transcription', fall back to 'segments'
    raw_segs = data.get('transcription') or data.get('segments', [])

    if not raw_segs:
        # Last resort: some builds use a flat array at top level
        if isinstance(data, list):
            raw_segs = data
        elif isinstance(data, dict):
            # Try other known keys
            for key in ('result', 'text'):
                val = data.get(key)
                if isinstance(val, list):
                    raw_segs = val
                    break

    segs = []
    for seg in raw_segs:
        # ── Timestamps: handle both nesting styles ──
        # Newer: seg['timestamps']['from'] / seg['timestamps']['to']
        # Older:  seg['from'] / seg['to'] (flat)
        # Also:   seg['start'] / seg['end'] (seconds as float or string)
        ts = seg.get('timestamps', seg)  # fall back to seg itself if no 'timestamps' sub-key

        ts_from = ts.get('from', ts.get('start', '00:00:00,000'))
        ts_to = ts.get('to', ts.get('end', '00:00:08,000'))

        # Convert to seconds if needed (handle both string timecodes and float seconds)
        if isinstance(ts_from, str):
            start_s = _to_seconds(ts_from)
        else:
            start_s = float(ts_from)
        if isinstance(ts_to, str):
            end_s = _to_seconds(ts_to)
        else:
            end_s = float(ts_to)

        text = seg.get('text', '').strip()
        if not text:
            continue

        segs.append({
            'start_s': start_s,
            'end_s': end_s,
            'text': text,
            'no_speech_prob': seg.get('no_speech_prob', -1.0),
            'avg_logprob': seg.get('avg_logprob', None),
            'compression_ratio': seg.get('compression_ratio', None),
        })

    return segs


# ═══════════════════════════════════════════════════════════════
# Backend: faster-whisper (CTranslate2)
# ═══════════════════════════════════════════════════════════════

def _transcribe_faster_whisper(audio_path, model_path, language,
                               beam_size=5, best_of=8,
                               nth=0.6, no_fallback=False,
                               **kwargs):
    """Transcribe via faster-whisper Python API. Returns unified segment list."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print('⚠ faster-whisper not installed. Run: pip install faster-whisper',
              file=sys.stderr)
        return []

    try:
        # Compute compute_type based on available hardware
        compute_type = kwargs.pop('compute_type', 'auto')
        cpu_threads = kwargs.pop('cpu_threads', 0) or 0

        model = WhisperModel(model_path, device='auto', compute_type=compute_type,
                            cpu_threads=cpu_threads)

        vad_filter = nth > 0
        segments, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=beam_size,
            best_of=best_of,
            vad_filter=vad_filter,
            vad_parameters=dict(
                threshold=nth,
                min_speech_duration_ms=300,
                min_silence_duration_ms=500,
            ) if vad_filter else None,
            temperature=0.0 if no_fallback else [0.0, 0.2, 0.4],
            without_timestamps=False,
        )

        segs = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            segs.append({
                'start_s': seg.start,
                'end_s': seg.end,
                'text': text,
                'no_speech_prob': seg.no_speech_prob,
                # faster-whisper uses 'avg_log_prob' (note underscore placement)
                'avg_logprob': getattr(seg, 'avg_log_prob', None),
                'compression_ratio': getattr(seg, 'compression_ratio', None),
            })

        return segs

    except Exception as e:
        print(f'⚠ faster-whisper transcription failed: {e}', file=sys.stderr)
        return []


# ═══════════════════════════════════════════════════════════════
# Backend: openai-whisper (PyTorch)
# ═══════════════════════════════════════════════════════════════

def _transcribe_openai_whisper(audio_path, model_path, language,
                               beam_size=5, best_of=8,
                               nth=0.6, no_fallback=False,
                               **kwargs):
    """Transcribe via openai-whisper Python API. Returns unified segment list."""
    try:
        import whisper
    except ImportError:
        print('⚠ openai-whisper not installed. Run: pip install openai-whisper',
              file=sys.stderr)
        return []

    try:
        model = whisper.load_model(model_path)

        # openai-whisper doesn't have nth (no-speech threshold) as a direct param
        # It uses `logprob_threshold` and `no_speech_threshold` in some versions
        decode_options = {
            'language': language,
            'beam_size': beam_size,
            'best_of': best_of,
            'temperature': 0.0 if no_fallback else [0.0, 0.2, 0.4],
        }
        if nth is not None and nth > 0:
            decode_options['no_speech_threshold'] = nth

        result = model.transcribe(audio_path, **decode_options)

        segs = []
        for seg in result.get('segments', []):
            text = seg.get('text', '').strip()
            if not text:
                continue
            segs.append({
                'start_s': seg.get('start', 0.0),
                'end_s': seg.get('end', 0.0),
                'text': text,
                'no_speech_prob': seg.get('no_speech_prob', -1.0),
                'avg_logprob': seg.get('avg_logprob', None),
                'compression_ratio': seg.get('compression_ratio', None),
            })

        return segs

    except Exception as e:
        print(f'⚠ openai-whisper transcription failed: {e}', file=sys.stderr)
        return []


# ═══════════════════════════════════════════════════════════════
# Backend validation: transcribe a short silent clip to verify
# ═══════════════════════════════════════════════════════════════

def validate_backend(backend_id, model_path, whisper_cli=None):
    """Quick smoke test: run a 1-second silent clip and check for errors.

    Returns:
        (ok: bool, message: str)
    """
    import tempfile
    import wave
    import struct

    # Generate 1 second of silence (16kHz mono 16-bit)
    tmpdir = tempfile.mkdtemp()
    test_wav = os.path.join(tmpdir, '_test_silence.wav')

    try:
        sample_rate = 16000
        with wave.open(test_wav, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(struct.pack('<' + 'h' * sample_rate, *([0] * sample_rate)))

        segs = transcribe(test_wav, model_path, language='ja',
                         backend=backend_id, threads=1, processors=1,
                         beam_size=1, best_of=1)

        # Silence should produce 0 segments (or very few with high no_speech_prob)
        return True, f'OK (returned {len(segs)} segments from silence)'

    except Exception as e:
        return False, f'Validation failed: {e}'
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# Backend detection summary (for init wizard)
# ═══════════════════════════════════════════════════════════════

def backend_detection_report():
    """Generate a detailed detection report for the init wizard.

    Returns:
        dict with keys:
          - available: list of ready backend IDs
          - not_available: list of backend IDs that could be installed
          - recommendations: str with install guidance
          - details: {backend_id: {installed, version, path, ...}}
    """
    available = []
    details = {}

    for bid, info in BACKEND_INFO.items():
        detail = {'backend_id': bid, 'installed': False, **info}
        try:
            if _check_backend(bid):
                detail['installed'] = True
                available.append(bid)

                # Get version if possible
                if bid == 'whisper-cpp':
                    ver = detect_whisper_cpp_version()
                    if ver:
                        detail['version'] = f'v{ver[0]}.{ver[1]}.{ver[2]}'
                    cli = os.environ.get('WHISPER_CLI', '')
                    detail['path'] = cli if cli and os.path.isfile(cli) else '(on PATH)'
                elif bid in ('faster-whisper', 'openai-whisper'):
                    detail['path'] = '(Python package)'
        except Exception as e:
            detail['error'] = str(e)
        details[bid] = detail

    not_available = [bid for bid in BACKEND_INFO if bid not in available]

    # Build recommendations
    if not available:
        recommendations = (
            '未检测到任何 Whisper 后端。请安装以下任一：\n'
            '  1. whisper.cpp (推荐): 下载 whisper-cli + GGML 模型\n'
            '     https://github.com/ggerganov/whisper.cpp/releases\n'
            '  2. faster-whisper: pip install faster-whisper\n'
            '  3. openai-whisper: pip install openai-whisper'
        )
    else:
        recommendations = f'可用后端: {", ".join(available)}'

    return {
        'available': available,
        'not_available': not_available,
        'recommendations': recommendations,
        'details': details,
    }
