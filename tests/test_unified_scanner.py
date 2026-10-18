"""Tests for scan/unified_scanner.py — repeat/gap/parse/scan/CLI helpers."""

import json
import os
import subprocess
import sys
import types
from collections import defaultdict
from pathlib import Path

import pytest

import scan.unified_scanner as scanner
from lib import whisper_utils
from scan.unified_scanner import (
    _compute_gap_statistics, _find_repeats, _find_video_for_srt,
    _is_excluded_repeat, _parse_episodes, _save_vad_cache, _should_scan,
    extract_speech_timeline, get_oped_boundaries, main, scan_all, scan_file,
    write_findings_json, write_issues,
)

from lib.config import ED_BOUNDARY_SEC, OP_BOUNDARY_SEC


# ═══════════════════════════════════════════════════════════════
# Fixtures / helpers for scan_all, VAD and CLI tests
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _no_real_tokenizers(monkeypatch):
    """Keep these unit tests off the real janome/jieba dictionaries.

    Term extraction lazily loads these external tokenizers.  Tests that assert
    term extraction install a fake at that seam explicitly; everything else
    falls back to the built-in n-gram path.
    """
    monkeypatch.setattr(scanner, '_janome_initialized', True)
    monkeypatch.setattr(scanner, '_janome_available', False)
    monkeypatch.setattr(scanner, '_jieba_initialized', True)
    monkeypatch.setattr(scanner, '_jieba_available', False)


def _install_fake_jieba(monkeypatch, mapping):
    """Install a fake ``jieba`` module; mapping: text → lcut result."""
    fake = types.ModuleType('jieba')
    fake.initialize = lambda: None
    fake.lcut = lambda text: list(mapping.get(text, []))
    monkeypatch.setitem(sys.modules, 'jieba', fake)
    monkeypatch.setattr(scanner, '_jieba_initialized', False)
    monkeypatch.setattr(scanner, '_jieba_available', False)


def _install_fake_janome(monkeypatch, mapping):
    """Install a fake ``janome.tokenizer``; mapping: text → [(surface, pos)]."""
    class _Token:
        def __init__(self, surface, pos):
            self.surface = surface
            self.part_of_speech = pos

    class _Tokenizer:
        def tokenize(self, text):
            return [_Token(s, p) for s, p in mapping.get(text, [])]

    package = types.ModuleType('janome')
    tokenizer_module = types.ModuleType('janome.tokenizer')
    tokenizer_module.Tokenizer = _Tokenizer
    package.tokenizer = tokenizer_module
    monkeypatch.setitem(sys.modules, 'janome', package)
    monkeypatch.setitem(sys.modules, 'janome.tokenizer', tokenizer_module)
    monkeypatch.setattr(scanner, '_janome_initialized', False)
    monkeypatch.setattr(scanner, '_janome_available', False)
    monkeypatch.setattr(scanner, '_tokenizer', None)


def _episode_srt(text='こんにちは hello'):
    """A 24-minute episode: one candidate cue at 10:00, one clean tail cue."""
    return (
        f"1\n00:10:00,000 --> 00:10:02,000\n{text}\n\n"
        "2\n00:24:00,000 --> 00:24:02,000\nおわり\n\n"
    )


def _single_cue_srt(text):
    return f"1\n00:10:00,000 --> 00:10:02,000\n{text}\n\n"


def _write_srt(directory, name, text):
    path = Path(directory) / name
    path.write_text(text, encoding='utf-8')
    return path


# A full episode with OP (start), a clean kanji cue, one garbled main cue,
# an ED cue near the end, and a clean tail cue (sets the ED boundary).
OP_ED_SRT = (
    "1\n00:00:10,000 --> 00:00:12,000\nOP song lyric\n\n"
    "2\n00:03:20,000 --> 00:03:22,000\n東京の街\n\n"
    "3\n00:10:00,000 --> 00:10:02,000\nこんにちは hello\n\n"
    "4\n00:21:40,000 --> 00:21:42,000\nED credits roll\n\n"
    "5\n00:24:00,000 --> 00:24:02,000\nおわり\n\n"
)

SECOND_EPISODE_SRT = (
    "1\n00:10:00,000 --> 00:10:02,000\nsecond episode line\n\n"
    "2\n00:24:00,000 --> 00:24:02,000\n" + "あいう" * 10 + "\n\n"
)


def cue(start, end, text='x'):
    return {'start': f'00:00:{start:02d},000', 'end': f'00:00:{end:02d},000',
            'start_s': float(start), 'end_s': float(end), 'text': text}


# ═══════════════════════════════════════════════════════════════
# Episode parsing / filtering
# ═══════════════════════════════════════════════════════════════

def test_parse_episodes_ranges_and_lists():
    assert _parse_episodes('EP001-EP003') == ['EP001', 'EP002', 'EP003']
    assert _parse_episodes('1,3') == ['EP001', 'EP003']
    assert _parse_episodes('5') == ['EP005']
    assert _parse_episodes(None) is None


def test_should_scan():
    assert _should_scan('EP001', None) is True
    assert _should_scan('EP001', ['EP001']) is True
    assert _should_scan('EP002', ['EP001']) is False


# ═══════════════════════════════════════════════════════════════
# Gap statistics / OP-ED boundaries
# ═══════════════════════════════════════════════════════════════

def test_gap_statistics_counts_suspicious_gaps():
    stats = _compute_gap_statistics([
        cue(0, 5), cue(6, 10), cue(40, 45),
    ])
    assert stats['total_gaps'] == 2
    assert stats['suspicious_gap_count'] == 1
    assert stats['max_gap_s'] == 30.0

    assert _compute_gap_statistics([cue(0, 5)])['total_gaps'] == 0


def test_oped_boundaries_follow_config():
    cues = [cue(0, 10), cue(700, 710)]
    op, ed = get_oped_boundaries(cues)
    assert op == OP_BOUNDARY_SEC
    assert ed == 710 - ED_BOUNDARY_SEC


# ═══════════════════════════════════════════════════════════════
# Repeat detection
# ═══════════════════════════════════════════════════════════════

def test_find_repeats_detects_loop():
    text = 'あいう' * 10
    found = _find_repeats(text, min_repeats=8)
    assert found
    assert found[0]['repeat_seq'] == 'あいう'
    assert found[0]['repeat_count'] == 10


def test_find_repeats_ignores_short_text():
    assert _find_repeats('あいう', min_repeats=8) == []


def test_excluded_repeats():
    assert _is_excluded_repeat('pa', 'papapapa') is True
    assert _is_excluded_repeat('あい', 'あいあい') is True     # only 2 chars
    assert _is_excluded_repeat('あいう', 'あいう' * 8) is False


# ═══════════════════════════════════════════════════════════════
# scan_file
# ═══════════════════════════════════════════════════════════════

SRT = (
    "1\n00:00:10,000 --> 00:00:12,000\nこんにちは\n\n"
    "2\n00:06:40,000 --> 00:06:42,000\nhello world\n\n"
    "3\n00:11:40,000 --> 00:11:42,000\nこんばんは\n\n"
)


def test_scan_file_flags_garbled_outside_oped(tmp_path):
    path = tmp_path / 'EP001.srt'
    path.write_text(SRT, encoding='utf-8')
    result = scan_file(str(path), skip_oped=True, target_lang='ja')

    assert result['filename'] == 'EP001.srt'
    assert result['total_cues'] == 3
    assert len(result['issues']) == 1
    assert result['issues'][0]['start'].startswith('00:06:40')
    assert len(result['garbled_cues']) == 1
    assert 'gap_statistics' in result


def test_scan_file_empty_file(tmp_path):
    path = tmp_path / 'empty.srt'
    path.write_text('', encoding='utf-8')
    result = scan_file(str(path))
    assert result['total_cues'] == 0
    assert result['issues'] == []


# ═══════════════════════════════════════════════════════════════
# Tokenizer segmentation (fake jieba / janome, n-gram fallback)
# ═══════════════════════════════════════════════════════════════

def test_segment_text_zh_prefers_jieba(monkeypatch):
    _install_fake_jieba(monkeypatch, {'你好世界': ['你好', '世界', '的', 'A']})
    freq = defaultdict(int)
    scanner._segment_text_zh('你好世界', freq)
    assert dict(freq) == {'你好': 1, '世界': 1}


def test_segment_text_zh_falls_back_to_ngrams():
    freq = defaultdict(int)
    scanner._segment_text_zh('你好世界', freq)
    assert dict(freq) == {'你好世界': 1}


def test_segment_text_ja_prefers_janome(monkeypatch):
    _install_fake_janome(monkeypatch, {'サリーと太郎': [
        ('サリー', '名詞,固有名詞,一般,*'),
        ('と', '助詞,格助詞,一般,*'),
        ('太郎', '名詞,固有名詞,人名,*'),
        ('あまり', '名詞,非自立,一般,*'),
    ]})
    freq = defaultdict(int)
    scanner._segment_text_ja('サリーと太郎', freq)
    assert dict(freq) == {'サリー': 1, '太郎': 1}


def test_segment_text_ja_skips_text_without_cjk(monkeypatch):
    _install_fake_janome(monkeypatch, {})
    freq = defaultdict(int)
    scanner._segment_text_ja('hello world', freq)
    assert dict(freq) == {}


def test_segment_text_ja_falls_back_to_ngrams():
    freq = defaultdict(int)
    scanner._segment_text_ja('サリーと太郎', freq)
    assert freq['サリー'] == 1
    assert '太郎' in freq


def test_tokenizer_init_failure_is_cached(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'janome.tokenizer', None)
    monkeypatch.setattr(scanner, '_janome_initialized', False)
    monkeypatch.setattr(scanner, '_janome_available', False)
    assert scanner._ensure_janome() is False
    assert 'Janome unavailable' in capsys.readouterr().err

    monkeypatch.setitem(sys.modules, 'jieba', None)
    monkeypatch.setattr(scanner, '_jieba_initialized', False)
    monkeypatch.setattr(scanner, '_jieba_available', False)
    assert scanner._ensure_jieba() is False
    assert 'jieba unavailable' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# OP/ED boundaries / video matching / speech timeline
# ═══════════════════════════════════════════════════════════════

def test_oped_boundaries_empty_cues():
    assert get_oped_boundaries([]) == (OP_BOUNDARY_SEC, 0)


def test_find_video_by_episode_number(tmp_path):
    videos = tmp_path / 'videos'
    videos.mkdir()
    (videos / 'Show_EP001.mkv').write_bytes(b'')
    assert _find_video_for_srt(
        str(tmp_path / 'EP001.srt'), str(videos)).endswith('Show_EP001.mkv')

    assert _find_video_for_srt(str(tmp_path / 'EP009.srt'), str(videos)) is None
    assert _find_video_for_srt(str(tmp_path / 'EP001.srt'), None) is None
    assert _find_video_for_srt(
        str(tmp_path / 'EP001.srt'), str(tmp_path / 'missing')) is None


def test_find_video_by_stem_when_episode_is_unknown(tmp_path):
    videos = tmp_path / 'videos'
    videos.mkdir()
    (videos / 'movie_BDRip.mp4').write_bytes(b'')
    assert _find_video_for_srt(
        str(tmp_path / 'movie.srt'), str(videos)).endswith('movie_BDRip.mp4')
    assert _find_video_for_srt(
        str(tmp_path / 'other.srt'), str(videos)) is None


def test_extract_speech_timeline_without_cues(monkeypatch):
    monkeypatch.setattr(scanner, 'parse_subtitles', lambda *a, **k: [])
    speech, stats = extract_speech_timeline('EP001.srt', 'ep.mkv', 'tmp')
    assert speech == []
    assert stats == {'error': 'No cues in subtitle file'}


def test_extract_speech_timeline_audio_failure(monkeypatch, capsys):
    monkeypatch.setattr(scanner, 'parse_subtitles',
                        lambda *a, **k: [{'text': 'x'}])

    def boom(video, out):
        raise RuntimeError('ffmpeg missing')

    monkeypatch.setattr(scanner, 'extract_audio_wav', boom)
    speech, stats = extract_speech_timeline('EP001.srt', 'ep.mkv', 'tmp')
    assert speech == []
    assert stats['error'] == 'ffmpeg missing'
    assert 'Audio extraction failed' in capsys.readouterr().err


def test_extract_speech_timeline_without_speech(monkeypatch):
    import fix.whisper_pipeline as pipeline

    monkeypatch.setattr(scanner, 'parse_subtitles',
                        lambda *a, **k: [{'text': 'x'}])
    monkeypatch.setattr(scanner, 'extract_audio_wav', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'get_speech_timeline', lambda audio: [])
    speech, stats = extract_speech_timeline('EP001.srt', 'ep.mkv', 'tmp')
    assert speech == []
    assert stats == {'audio_duration_s': 0, 'speech_duration_s': 0,
                     'speech_pct': 0}


def test_extract_speech_timeline_computes_stats(monkeypatch):
    import fix.whisper_pipeline as pipeline

    monkeypatch.setattr(scanner, 'parse_subtitles',
                        lambda *a, **k: [{'text': 'x'}])
    monkeypatch.setattr(scanner, 'extract_audio_wav', lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'get_speech_timeline',
                        lambda audio: [(0.0, 2.0), (3.0, 4.0)])
    monkeypatch.setattr(whisper_utils, 'get_audio_duration',
                        lambda path: 10.0)
    speech, stats = extract_speech_timeline('EP001.srt', 'ep.mkv', 'tmp')
    assert speech == [(0.0, 2.0), (3.0, 4.0)]
    assert stats == {'audio_duration_s': 10.0, 'speech_duration_s': 3.0,
                     'speech_pct': 30.0}


# ═══════════════════════════════════════════════════════════════
# scan_all aggregation + VAD cache
# ═══════════════════════════════════════════════════════════════

SCAN_ALL_EP1 = (
    "1\n00:00:10,000 --> 00:00:12,000\nOP song lyric\n\n"
    "2\n00:03:20,000 --> 00:03:22,000\n東京の街\n\n"
    "3\n00:10:00,000 --> 00:10:02,000\nこんにちは hello\n\n"
    "4\n00:22:30,000 --> 00:22:32,000\nED credits roll\n\n"
    "5\n00:24:00,000 --> 00:24:02,000\nおわり\n\n"
)

SCAN_ALL_EP2 = (
    "1\n00:10:00,000 --> 00:10:02,000\nこんにちは\n\n"
    "2\n00:24:00,000 --> 00:24:02,000\nおわり\n\n"
)


def test_scan_all_aggregates_episodes(tmp_path):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    _write_srt(tmp_path, 'EP002.srt', SCAN_ALL_EP2)

    result = scan_all(str(tmp_path), target_lang='ja')

    assert result['summary'] == {
        'files_scanned': 2,
        'total_cues': 7,
        'garbled_count': 1,
        'repeat_count': 0,
        'term_count': result['summary']['term_count'],
        'episodes_with_issues': 1,
        'vad_episodes_scanned': 0,
    }
    assert set(result['per_episode_issues']) == {'EP001'}
    assert result['per_episode_issues']['EP001'][0]['original_text'] == \
        'こんにちは hello'
    assert result['garbled_cues'][0]['file'] == 'EP001.srt'
    assert set(result['gap_statistics']) == {'EP001', 'EP002'}
    assert result['summary']['term_count'] > 0


def test_scan_all_episode_filter(tmp_path):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    _write_srt(tmp_path, 'EP002.srt', SCAN_ALL_EP2)

    result = scan_all(str(tmp_path), target_lang='ja', episodes=['EP002'])

    assert result['summary']['files_scanned'] == 1
    assert result['summary']['total_cues'] == 2
    assert result['summary']['garbled_count'] == 0
    assert result['per_episode_issues'] == {}
    assert set(result['gap_statistics']) == {'EP002'}


def test_scan_all_empty_directory(tmp_path):
    result = scan_all(str(tmp_path), target_lang='ja')
    assert result['summary']['files_scanned'] == 0
    assert result['summary']['total_cues'] == 0
    assert result['garbled_cues'] == []
    assert result['per_episode_issues'] == {}


def test_scan_all_vad_caches_speech_timeline(monkeypatch, tmp_path):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    _write_srt(tmp_path, 'EP002.srt', SCAN_ALL_EP2)
    videos = tmp_path / 'videos'
    videos.mkdir()
    (videos / 'EP001.mkv').write_bytes(b'')

    monkeypatch.setattr(
        scanner, '_find_video_for_srt',
        lambda srt, vd: str(videos / 'EP001.mkv')
        if srt.endswith('EP001.srt') else None)
    monkeypatch.setattr(
        scanner, 'extract_speech_timeline',
        lambda srt, video, tmp: ([(0.0, 5.0)], {'speech_pct': 50.0}))

    cache = tmp_path / 'cache'
    result = scan_all(str(tmp_path), target_lang='ja', video_dir=str(videos),
                      vad_cache_dir=str(cache))

    assert result['summary']['vad_episodes_scanned'] == 1
    data = json.loads((cache / 'EP001_vad.json').read_text(encoding='utf-8'))
    assert data['speech_segs'] == [[0.0, 5.0]]
    assert data['stats'] == {'speech_pct': 50.0}
    assert not (cache / 'EP002_vad.json').exists()


def test_scan_all_skip_vad(monkeypatch, tmp_path, capsys):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    videos = tmp_path / 'videos'
    videos.mkdir()

    def fail(*args, **kwargs):
        raise AssertionError('VAD must be skipped')

    monkeypatch.setattr(scanner, 'extract_speech_timeline', fail)

    result = scan_all(str(tmp_path), target_lang='ja', video_dir=str(videos),
                      skip_vad=True)

    assert result['summary']['vad_episodes_scanned'] == 0
    assert '--skip-vad' in capsys.readouterr().err


def test_scan_all_vad_error_is_reported(monkeypatch, tmp_path, capsys):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    videos = tmp_path / 'videos'
    videos.mkdir()

    monkeypatch.setattr(scanner, '_find_video_for_srt',
                        lambda srt, vd: str(videos / 'EP001.mkv'))
    monkeypatch.setattr(scanner, 'extract_speech_timeline',
                        lambda *a, **k: ([], {'error': 'no audio'}))

    result = scan_all(str(tmp_path), target_lang='ja', video_dir=str(videos))

    assert result['summary']['vad_episodes_scanned'] == 0
    assert 'VAD failed: no audio' in capsys.readouterr().err


def test_save_vad_cache_writes_and_warns_on_failure(tmp_path, capsys):
    path = tmp_path / 'nested' / 'EP001_vad.json'
    _save_vad_cache(str(path), [(0.0, 1.0)], {'speech_pct': 10.0})
    assert json.loads(path.read_text(encoding='utf-8')) == {
        'speech_segs': [[0.0, 1.0]], 'stats': {'speech_pct': 10.0}}

    blocker = tmp_path / 'blocker'
    blocker.write_text('not a dir', encoding='utf-8')
    _save_vad_cache(str(blocker / 'sub' / 'x.json'), [], {})
    assert 'Failed to save' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Output writers
# ═══════════════════════════════════════════════════════════════

def test_write_findings_and_issues(tmp_path, capsys):
    output = {
        'garbled_cues': [],
        'per_episode_issues': {
            'EP001': [{'ep': 'EP001', 'line': 3}],
            '???': [{'ep': '???'}],
            '': [],
        },
        'summary': {'garbled_count': 1},
    }
    findings = tmp_path / 'findings.json'
    write_findings_json(output, str(findings))
    assert json.loads(findings.read_text(encoding='utf-8')) == output

    issues_dir = tmp_path / 'issues'
    write_issues(output, str(issues_dir))

    data = json.loads(
        (issues_dir / 'issues_EP001.json').read_text(encoding='utf-8'))
    assert data == {
        'episode': 'EP001',
        'issue_count': 1,
        'issues': [{'ep': 'EP001', 'line': 3}],
        'source': 'unified_scanner.py',
    }
    assert not (issues_dir / 'issues_???.json').exists()
    assert '跳过无效ID' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# CLI main
# ═══════════════════════════════════════════════════════════════

def _empty_result():
    return {
        'garbled_cues': [],
        'per_episode_issues': {},
        'repeats': [],
        'term_frequencies': {},
        'gap_statistics': {},
        'summary': {
            'files_scanned': 0, 'total_cues': 0, 'garbled_count': 0,
            'repeat_count': 0, 'term_count': 0,
            'episodes_with_issues': 0, 'vad_episodes_scanned': 0,
        },
    }


def test_main_writes_findings_and_issues(monkeypatch, tmp_path, capsys):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    findings = tmp_path / 'findings.json'
    issues = tmp_path / 'issues'
    monkeypatch.setattr(sys, 'argv', [
        'unified_scanner.py', '--target-dir', str(tmp_path),
        '--output-findings', str(findings),
        '--output-issues', str(issues),
        '--project-lang', 'ja'])

    main()

    data = json.loads(findings.read_text(encoding='utf-8'))
    assert data['summary']['garbled_count'] == 1
    assert (issues / 'issues_EP001.json').exists()

    err = capsys.readouterr().err
    assert '=== 扫描完成 ===' in err
    assert '→ findings:' in err
    assert '→ issues:' in err


def test_main_prints_findings_to_stdout(monkeypatch, tmp_path, capsys):
    _write_srt(tmp_path, 'EP001.srt', SCAN_ALL_EP1)
    monkeypatch.setattr(sys, 'argv', [
        'unified_scanner.py', '--target-dir', str(tmp_path), '--skip-vad'])

    main()

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data['summary']['garbled_count'] == 1
    assert '扫描目录' in captured.err


def test_main_rejects_missing_target_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, 'argv', [
        'unified_scanner.py', '--target-dir', str(tmp_path / 'missing')])

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert '目录不存在' in capsys.readouterr().err


def test_main_passes_filters_to_scan_all(monkeypatch, tmp_path, capsys):
    captured = {}
    videos = tmp_path / 'videos'
    videos.mkdir()

    def fake_scan_all(target, **kwargs):
        captured.update(kwargs)
        captured['target'] = target
        return _empty_result()

    monkeypatch.setattr(scanner, 'scan_all', fake_scan_all)
    monkeypatch.setattr(sys, 'argv', [
        'unified_scanner.py', '--target-dir', str(tmp_path),
        '--project-lang', 'zh', '--episodes', 'EP001-EP002',
        '--no-skip-oped', '--video-dir', str(videos), '--skip-vad',
        '--vad-cache-dir', str(tmp_path / 'cache')])

    main()

    assert captured['target'] == str(tmp_path)
    assert captured['target_lang'] == 'zh'
    assert captured['episodes'] == ['EP001', 'EP002']
    assert captured['skip_oped'] is False
    assert captured['video_dir'] == str(videos)
    assert captured['skip_vad'] is True
    assert captured['vad_cache_dir'] == str(tmp_path / 'cache')
    assert '限定集数: 2 集' in capsys.readouterr().err


def test_main_build_glossary_invokes_helper(monkeypatch, tmp_path, capsys):
    findings = tmp_path / 'findings.json'
    ai_nouns = tmp_path / 'ai_nouns.json'
    ai_nouns.write_text('[]', encoding='utf-8')
    glossary = tmp_path / 'reports' / 'proper-nouns.md'
    captured = {}

    monkeypatch.setattr(scanner, 'scan_all', lambda *a, **k: _empty_result())

    import subprocess as sp
    monkeypatch.setattr(
        sp, 'run', lambda cmd, **kwargs: captured.update(
            cmd=cmd, kwargs=kwargs))

    monkeypatch.setattr(sys, 'argv', [
        'unified_scanner.py', '--target-dir', str(tmp_path),
        '--output-findings', str(findings), '--build-glossary',
        '--glossary-output', str(glossary), '--ai-nouns', str(ai_nouns),
        '--project-lang', 'zh'])

    main()

    cmd = captured['cmd']
    assert cmd[0] == sys.executable
    assert cmd[1].endswith(str(Path('nouns') / 'build_glossary.py'))
    assert '--findings' in cmd and str(findings) in cmd
    assert '--output' in cmd and str(glossary) in cmd
    assert '--lang' in cmd and 'zh' in cmd
    assert '--ai-nouns' in cmd and str(ai_nouns) in cmd
    assert captured['kwargs'] == {'check': False}
    assert '生成术语表' in capsys.readouterr().err
