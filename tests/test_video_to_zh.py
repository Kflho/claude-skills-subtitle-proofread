"""Tests for the video→Japanese SRT→Chinese orchestrator (no model/API)."""

from pathlib import Path

import video_to_zh as v


def test_qwen3_transcribe_command(tmp_path):
    cmd = v.build_transcribe_cmd(
        'qwen3', 'video.mkv', str(tmp_path / 'ja' / 'EP001.srt'),
        python='py.exe', model='models/asr', aligner='models/fa')
    joined = ' '.join(cmd)
    assert 'qwen3_transcribe.py' in joined
    assert '--video video.mkv' in joined
    assert '--srt' in joined and 'EP001.srt' in joined
    assert '--output' in joined and 'EP001.asr.json' in joined
    assert '--python py.exe' in joined
    assert '--model models/asr' in joined


def test_translate_command(tmp_path):
    cmd = v.build_translate_cmd(
        'ja.srt', 'zh.srt', 'qa.json', mappings='g.json', batch=10,
        repair=True)
    joined = ' '.join(cmd)
    assert 'translate_srt.py' in joined
    assert '--input ja.srt' in joined
    assert '--output zh.srt' in joined
    assert '--mappings g.json' in joined
    assert '--repair' in joined
    assert '--batch 10' in joined


def test_dry_run_selects_videos(tmp_path, capsys):
    video_dir = tmp_path / 'videos'
    video_dir.mkdir()
    (video_dir / '01、test.mkv').write_bytes(b'')
    out = tmp_path / 'out'
    code = v.main(['--video-dir', str(video_dir), '--output-dir', str(out),
                   '--dry-run'])
    assert code == 0
    assert (out / 'ja').is_dir() and (out / 'zh').is_dir()
    captured = capsys.readouterr()
    assert '[transcribe]' in captured.err
    assert '[translate]' in captured.err
    assert 'DRY RUN' in captured.err
