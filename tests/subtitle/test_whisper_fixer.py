"""Tests for fix/whisper_fixer.py — VAD cache, tier routing, triage, translation."""

from types import SimpleNamespace

import pytest

from fix import whisper_fixer as wf
from fix import whisper_pipeline as wp


def cue(start, end, text, garbled=True, line=1):
    return {'start': f'{start:09.3f}', 'end': f'{end:09.3f}',
            'start_s': float(start), 'end_s': float(end),
            'text': text, 'is_garbled': garbled, 'line': line}


def _session(tmp_path, **overrides):
    saved = []
    base = dict(
        episode='EP001',
        srt_path=str(tmp_path / 'EP001.srt'),
        video_path=str(tmp_path / 'EP001.mkv'),
        srt_dir=str(tmp_path),
        temp_dir=str(tmp_path / 'temp' / 'scans'),
        target_lang='ja',
        whisper_cli='whisper-cli',
        model='model.bin',
        retry_model=None,
        video_dir=str(tmp_path),
        load_speech_segs=lambda: [],
        save_speech_segs=lambda segs: saved.append(segs),
    )
    base.update(overrides)
    session = SimpleNamespace(**base)
    session.saved_speech = saved
    return session


def _fix(start, end, original, replacement, confidence='high'):
    return {'start': start, 'end': end, 'original': original,
            'replacement': replacement, 'confidence': confidence,
            'model': 'tier1'}


# ═══════════════════════════════════════════════════════════════
# Guard rails
# ═══════════════════════════════════════════════════════════════

def test_without_video_returns_detail(tmp_path):
    result = wf.WhisperFixer(_session(tmp_path,
                                      video_path=None)).fix_by_whisper()
    assert result.details == ['No video file found']
    assert result.applied == 0


def test_without_srt_returns_detail(tmp_path):
    result = wf.WhisperFixer(_session(tmp_path,
                                      srt_path=None)).fix_by_whisper()
    assert result.details == ['No SRT file to fix']


def test_all_clean_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, 'parse_srt',
                        lambda *a, **k: [cue(1, 2, 'こんにちは', garbled=False)])
    result = wf.WhisperFixer(_session(tmp_path)).fix_by_whisper()
    assert result.applied == 0 and result.tier == 0


# ═══════════════════════════════════════════════════════════════
# VAD cache gating
# ═══════════════════════════════════════════════════════════════

def test_cached_vad_respects_apply_gate(tmp_path, monkeypatch):
    cues = [cue(1, 2, 'bad')]
    captured = {}

    def fake_cache(speech_segs, cs, srt_path, target_lang='ja', apply=False):
        captured['apply'] = apply
        return cs, [cues[0]], speech_segs

    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    monkeypatch.setattr(wf, '_apply_vad_clean_from_cache', fake_cache)
    monkeypatch.setattr(wp, 'build_fix_regions', lambda *a, **k: [])

    session = _session(tmp_path)
    session.load_speech_segs = lambda: [(0.0, 5.0)]

    preview = wf.WhisperFixer(session).fix_by_whisper(vad_clean_apply=False)
    assert captured['apply'] is False
    assert preview.deleted == 0

    applied = wf.WhisperFixer(session).fix_by_whisper(vad_clean_apply=True)
    assert captured['apply'] is True
    assert applied.deleted == 1


def test_no_speech_timeline_stops_before_whisper(tmp_path, monkeypatch, capsys):
    cues = [cue(1, 2, 'bad')]

    def boom(*args, **kwargs):
        raise RuntimeError('ffmpeg missing')

    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    monkeypatch.setattr(wf, 'extract_audio_wav', boom)

    session = _session(tmp_path)
    result = wf.WhisperFixer(session).fix_by_whisper()

    assert result.applied == 0
    assert result.details == []
    assert '无语音时间线' in capsys.readouterr().err
    assert session.saved_speech == []


# ═══════════════════════════════════════════════════════════════
# Tier 1: triage
# ═══════════════════════════════════════════════════════════════

def _patch_region(monkeypatch, cues):
    region = {
        'start_s': 0.0, 'end_s': 20.0, 'type': 'garbled',
        'cues_to_clear': list(cues),
        'garbled_cues': list(cues),
        'clean_cues': [],
        'context_left': None, 'context_right': None,
    }
    monkeypatch.setattr(wp, 'build_fix_regions', lambda *a, **k: [region])
    monkeypatch.setattr(wf, '_apply_vad_clean_from_cache',
                        lambda segs, cs, path, target_lang='ja', apply=False:
                        (cs, [], segs))


def test_tier1_triage_classifies_fixes(tmp_path, monkeypatch, capsys):
    cues = [cue(10 + i, 11 + i, f'bad{i}') for i in range(4)]
    fixes = [
        _fix(cues[0]['start'], cues[0]['end'], 'bad0', 'よくできた'),      # keep
        _fix(cues[1]['start'], cues[1]['end'], 'おはよう',
             'おはようございます元気ですか'),                                # anomaly → AI
        _fix(cues[2]['start'], cues[2]['end'], 'bad2', 'abcd漢字efg'),      # corrupt → AI
        _fix(cues[3]['start'], cues[3]['end'], '。。。', None),              # cut
    ]
    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    _patch_region(monkeypatch, cues)
    monkeypatch.setattr(wp, 'run_tier1', lambda *a, **k: (fixes, []))

    session = _session(tmp_path)
    session.load_speech_segs = lambda: [(0.0, 20.0)]
    result = wf.WhisperFixer(session).fix_by_whisper()

    assert result.tier == 1
    assert [f['replacement'] for f in result.auto_keep_fixes] == ['よくできた']
    assert len(result.ai_fragments) == 2
    assert len(result.auto_cuts) == 1
    assert result.applied == 1
    assert result.ai_review == 2
    assert result.deleted == 1
    assert len(result.cues_to_clear) == 4
    assert '[cut]' in capsys.readouterr().err


def test_tier2_upgrade_above_threshold(tmp_path, monkeypatch):
    cues = [cue(10 + i, 11 + i, f'bad{i}') for i in range(3)]
    captured = {}

    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    _patch_region(monkeypatch, cues)
    monkeypatch.setattr(wp, 'UPGRADE_THRESHOLD', 1)

    def fake_tier2(video, cs, cli, model, lang, tmpdir, **kwargs):
        captured.update(kwargs)
        return [_fix(cs[0]['start'], cs[0]['end'], 'bad0', 'よくできた')], {}

    monkeypatch.setattr(wp, 'run_tier2', fake_tier2)

    session = _session(tmp_path)
    session.load_speech_segs = lambda: [(0.0, 20.0)]
    result = wf.WhisperFixer(session).fix_by_whisper()

    assert result.tier == 2
    assert captured['save_transcript_to'].endswith(
        'whisper_full_EP001.json')
    assert result.applied == 1


def test_no_whisper_output_falls_back_to_original_cues(tmp_path, monkeypatch):
    cues = [cue(10, 11, 'abc'), cue(12, 13, 'こんにちは')]
    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    _patch_region(monkeypatch, cues)
    monkeypatch.setattr(wp, 'run_tier1', lambda *a, **k: ([], cues))

    session = _session(tmp_path)
    session.load_speech_segs = lambda: [(0.0, 20.0)]
    result = wf.WhisperFixer(session).fix_by_whisper()

    # 'abc' has no meaningful ja chars → cut; 'こんにちは' → auto-keep
    assert len(result.auto_cuts) == 1
    assert [f['original'] for f in result.auto_keep_fixes] == ['こんにちは']


def test_retry_model_runs_second_tier1_pass(tmp_path, monkeypatch):
    cues = [cue(10, 11, 'bad')]
    calls = []
    first_fix = _fix(cues[0]['start'], cues[0]['end'], 'bad', None,
                     confidence='none')
    retry_fix = _fix(cues[0]['start'], cues[0]['end'], 'bad', 'よくできた')

    monkeypatch.setattr(wf, 'parse_srt', lambda *a, **k: cues)
    _patch_region(monkeypatch, cues)

    def fake_tier1(video, cs, clusters, cli, model, lang, tmpdir, **kwargs):
        calls.append(model)
        if len(calls) == 1:
            return [first_fix], cs
        return [retry_fix], []

    monkeypatch.setattr(wp, 'run_tier1', fake_tier1)

    session = _session(tmp_path, retry_model='retry.bin')
    session.load_speech_segs = lambda: [(0.0, 20.0)]
    result = wf.WhisperFixer(session).fix_by_whisper()

    assert calls == ['model.bin', 'retry.bin']
    assert [f['replacement'] for f in result.auto_keep_fixes] == ['よくできた']


# ═══════════════════════════════════════════════════════════════
# Cached VAD clean helper
# ═══════════════════════════════════════════════════════════════

def test_vad_cache_deletes_markers_and_silent_noise(tmp_path, monkeypatch,
                                                   capsys):
    cues = [
        cue(0, 1, '[音楽]'),          # marker → delete
        cue(2, 3, 'こんにちは'),      # plausible → keep despite no speech
        cue(8, 9, 'ABC'),            # no speech, implausible, short → delete
        cue(9, 12, 'ABCDEF'),        # no speech but >3s → keep
    ]

    kept, deleted, segs = wf._apply_vad_clean_from_cache(
        [(5.0, 6.0)], cues, str(tmp_path / 'EP001.srt'), apply=False)

    assert [c['text'] for c in deleted] == ['[音楽]', 'ABC']
    assert [c['text'] for c in kept] == ['こんにちは', 'ABCDEF']
    assert segs == [(5.0, 6.0)]
    assert '未写盘' in capsys.readouterr().err


def test_vad_cache_apply_writes_srt(tmp_path, monkeypatch):
    cues = [cue(0, 1, '[音楽]'), cue(2, 3, 'こんにちは')]
    written = []
    monkeypatch.setattr('lib.whisper_utils.write_srt',
                        lambda path, kept: written.append((path, kept)))
    monkeypatch.setattr('lib.project_utils.backup_file',
                        lambda path: None)

    kept, deleted, _ = wf._apply_vad_clean_from_cache(
        [(2.0, 3.0)], cues, 'ep.srt', apply=True)

    assert [c['text'] for c in kept] == ['こんにちは']
    assert written and written[0][1] == kept


# ═══════════════════════════════════════════════════════════════
# ja → target language replacement translation
# ═══════════════════════════════════════════════════════════════

def test_translate_replacements_skips_japanese():
    items = [{'replacement': 'おはよう'}]
    wf._translate_whisper_replacements(items, 'ja')
    assert items[0]['replacement'] == 'おはよう'


def test_translate_replacements_uses_llm(monkeypatch):
    monkeypatch.setattr(wf, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr('lib.llm.call_chat',
                        lambda *a, **k: '["译:おはよう"]')
    items = [{'replacement': 'おはよう'}, {'replacement': ''}]

    wf._translate_whisper_replacements(items, 'zh')

    assert items[0]['replacement'] == '译:おはよう'
    assert items[0]['whisper_original_ja'] == 'おはよう'
    assert items[1]['replacement'] == ''


def test_translate_replacements_without_llm_key_keeps_ja(monkeypatch,
                                                          capsys):
    monkeypatch.setattr(wf, 'LLM_API_KEY', '')
    items = [{'replacement': 'おはよう'}]

    wf._translate_whisper_replacements(items, 'zh')

    assert items[0]['replacement'] == 'おはよう'
    assert 'LLM_API_KEY not set' in capsys.readouterr().err


def test_translate_replacements_keeps_original_on_failed_llm(monkeypatch,
                                                             capsys):
    monkeypatch.setattr(wf, 'LLM_API_KEY', 'test-key')
    monkeypatch.setattr('lib.llm.call_chat', lambda *a, **k: None)
    items = [{'replacement': 'おはよう'}]

    wf._translate_whisper_replacements(items, 'zh')

    assert items[0]['replacement'] == 'おはよう'
    assert 'whisper_original_ja' not in items[0]
    assert 'kept Japanese' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# cues_to_clear
# ═══════════════════════════════════════════════════════════════

def test_collect_cues_to_clear_dedupes_by_start():
    c1 = cue(1, 2, 'a')
    c2 = cue(3, 4, 'b')
    regions = [
        {'cues_to_clear': [c1, c2]},
        {'cues_to_clear': [c1]},
        {'cues_to_clear': []},
    ]
    assert wf._collect_cues_to_clear(regions) == [c1, c2]
    assert wf._collect_cues_to_clear([]) == []
