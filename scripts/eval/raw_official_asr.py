#!/usr/bin/env python3
"""Raw Qwen3-ASR official long-audio path (chunk_sec=0), no skill chunking."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lib._path  # noqa: F401,E402
from lib.asr.qwen3 import Qwen3WorkerClient  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--python', default='.venv-qwen/Scripts/python.exe')
    parser.add_argument('--worker', default='scripts/asr/qwen3_worker.py')
    parser.add_argument('--asr', default='models/Qwen3-ASR-1.7B')
    parser.add_argument('--aligner', default='models/Qwen3-ForcedAligner-0.6B')
    args = parser.parse_args(argv)

    client = Qwen3WorkerClient(
        python_exe=os.path.abspath(args.python),
        worker_script=os.path.abspath(args.worker),
        project_root=os.getcwd(),
        asr_model=os.path.abspath(args.asr),
        aligner_model=os.path.abspath(args.aligner),
        request_timeout=1800,
    )
    try:
        client.start()
        client.load()
        result = client.transcribe(os.path.abspath(args.audio),
                                   language='ja', timestamps=True,
                                   chunk_sec=0)
    finally:
        client.close()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding='utf-8')
    print(f'wrote {args.out}: {len(result.tokens)} tokens')


if __name__ == '__main__':
    main()
