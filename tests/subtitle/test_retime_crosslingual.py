"""Tests for scripts/asr/retime_crosslingual.py.

The module retimes a subtitle whose text is already correct but whose timing
belongs to another cut/language.  Only the pure functions and the CLI with a
pre-built anchors file are exercised here — the LLM path is injected.
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from asr import retime_crosslingual as R  # noqa: E402


def cue(start, end, text='x'):
    return {'start_s': float(start), 'end_s': float(end), 'text': text,
            'start': '', 'end': ''}


def anchor(cue_start, delta, asr=0):
    return {'cue': 0, 'asr': asr, 'cue_start': float(cue_start),
            'asr_start': cue_start + delta, 'asr_end': cue_start + delta + 1.0,
            'asr_len': 1.0, 'delta': float(delta)}


# ── anchor parsing ──────────────────────────────────────────────────────
def test_parse_pairs_validates_ids():
    reply = ('[{"a": 0, "b": 1}, {"a": 99, "b": 1}, {"a": 1, "b": "x"}, '
             '{"a": 2, "b": null}, {"a": 3, "b": 77}]')
    pairs = R.parse_pairs(reply, cue_ids=[0, 1, 2, 3, 4], asr_ids=[0, 1, 2])
    assert pairs == {0: 1, 2: None, 3: None, 1: None, 4: None} or \
        pairs.get(0) == 1 and pairs.get(2) is None


def test_parse_pairs_rejects_garbage():
    with pytest.raises(ValueError):
        R.parse_pairs('not json at all', [0], [0])


def test_pairs_to_anchors_delta():
    cues = [cue(10, 12), cue(20, 22)]
    rows = [cue(12.5, 14, 'ja'), cue(30, 31, 'ja')]
    anchors = R.pairs_to_anchors({0: 0, 1: None}, cues, rows)
    assert len(anchors) == 1
    assert anchors[0]['delta'] == pytest.approx(2.5)
    assert anchors[0]['asr_len'] == pytest.approx(1.5)


# ── anchor hygiene ──────────────────────────────────────────────────────
def test_drop_nonmonotone_keeps_increasing_chain():
    anchors = [anchor(0, 0, asr=0), anchor(10, 0, asr=3),
               anchor(20, 0, asr=2), anchor(30, 0, asr=5)]
    chain = R.drop_nonmonotone(anchors)
    assert [a['asr'] for a in chain] == [0, 3, 5]


def test_filter_anchors_drops_local_outlier():
    deltas = [0.0, 0.1, -0.1, 9.0, 0.2, 0.0]
    anchors = [anchor(i * 30, d) for i, d in enumerate(deltas)]
    kept = R.filter_anchors(anchors, window=3, tol=3.0)
    assert len(kept) == 5
    assert all(abs(a['delta']) < 1 for a in kept)


def test_prepare_anchors_combines_both_rules():
    anchors = [anchor(0, 0.0, asr=0), anchor(30, 0.0, asr=1),
               anchor(60, 5.0, asr=0), anchor(90, 0.1, asr=2)]
    clean = R.prepare_anchors(anchors, window=3, tol=3.0)
    assert [a['asr'] for a in clean] == [0, 1, 2]


# ── warp ────────────────────────────────────────────────────────────────
def test_warp_constant_delta():
    warp = R.Warp([anchor(0, 2.0), anchor(100, 2.0), anchor(200, 2.0)])
    assert warp(0) == pytest.approx(2.0)
    assert warp(150) == pytest.approx(2.0)
    assert warp(999) == pytest.approx(2.0)


def test_warp_linear_interpolation():
    warp = R.Warp([anchor(0, 0.0), anchor(100, 10.0)], smooth=0)
    assert warp(50) == pytest.approx(5.0)


def test_warp_holds_ends():
    warp = R.Warp([anchor(100, 3.0), anchor(200, 4.0)], smooth=0)
    assert warp(0) == pytest.approx(3.0)
    assert warp(500) == pytest.approx(4.0)


def test_warp_smoothing_tames_single_spike():
    anchors = [anchor(0, 0.0), anchor(100, 0.0), anchor(200, 6.0),
               anchor(300, 0.0), anchor(400, 0.0)]
    warp = R.Warp(anchors, smooth=2)
    assert abs(warp(200)) < 1.0


def test_warp_points_rejects_bucket_outlier():
    anchors = [anchor(t, 0.0) for t in (0, 20, 40, 60)]
    anchors.append(anchor(30, 9.0))          # one bad pairing
    points = R.warp_points(anchors, bucket=90.0, min_count=3, tol=4.0)
    assert len(points) == 4
    assert all(p['delta'] == 0.0 for p in points)


def test_warp_points_keeps_sparse_anchors():
    anchors = [anchor(10, 1.0), anchor(20, 1.0)]   # fewer than min_count
    points = R.warp_points(anchors, bucket=90.0, min_count=3)
    assert len(points) == 2
    assert [p['delta'] for p in points] == [1.0, 1.0]


def test_warp_points_piecewise_offsets_survive():
    anchors = [anchor(t, 0.0) for t in (0, 20, 40)] + \
              [anchor(t, 5.0) for t in (200, 220, 240)]
    points = R.warp_points(anchors, bucket=90.0, min_count=3)
    assert [p['delta'] for p in points] == [0.0, 0.0, 0.0, 5.0, 5.0, 5.0]
    assert points[-1]['cue_start'] == 240


# ── apply ───────────────────────────────────────────────────────────────
def test_apply_warp_keeps_text_and_count():
    cues = [cue(10, 12, '第一行'), cue(20, 22, '第二行')]
    warp = R.Warp([anchor(0, 1.0), anchor(100, 1.0)])
    out = R.apply_warp(cues, warp)
    assert [c['text'] for c in out] == ['第一行', '第二行']
    assert out[0]['start_s'] == pytest.approx(11.0)
    assert out[0]['end_s'] == pytest.approx(13.0)


def test_apply_warp_never_overlaps():
    cues = [cue(10, 20), cue(20, 30)]
    # warp squeezes the second cue backwards
    warp = R.Warp([anchor(0, 0.0), anchor(20, -15.0)], smooth=0)
    out = R.apply_warp(cues, warp)
    assert out[0]['end_s'] <= out[1]['start_s']


def test_apply_warp_enforces_min_duration():
    cues = [cue(10, 10.05)]
    out = R.apply_warp(cues, R.Warp([anchor(0, 0.0)]), min_dur=0.4)
    assert out[0]['end_s'] - out[0]['start_s'] >= 0.4 - 1e-6


def test_apply_warp_keeps_legitimate_overlap():
    """Two lines can legitimately share time (sign + dialogue); don't split them."""
    cues = [cue(10, 20), cue(15, 25)]
    warp = R.Warp([anchor(0, 1.0), anchor(100, 1.0)])
    out = R.apply_warp(cues, warp)
    assert out[0]['start_s'] == pytest.approx(11.0)
    assert out[1]['start_s'] == pytest.approx(16.0)


def test_apply_warp_trims_previous_end_instead_of_delaying_start():
    cues = [cue(10, 20), cue(21, 30)]
    warp = R.Warp([anchor(0, 0.0), anchor(20, 0.0), anchor(21, -10.0)], smooth=0)
    out = R.apply_warp(cues, warp)
    assert out[1]['start_s'] == pytest.approx(11.0)       # mapped start kept
    assert out[0]['end_s'] < out[1]['start_s']            # tail trimmed


def test_apply_warp_allows_brief_overlap_over_delaying_start():
    """A big push is worse than a brief overlap when the tail cannot be trimmed."""
    cues = [cue(10, 10.1), cue(10.15, 12)]
    warp = R.Warp([anchor(10, 0.0), anchor(10.15, -1.0)], smooth=0)
    out = R.apply_warp(cues, warp)
    assert out[1]['start_s'] == pytest.approx(9.15, abs=0.02)   # mapped, not pushed
    assert out[0]['end_s'] > out[1]['start_s']                  # overlap accepted


def test_apply_warp_never_negative_start():
    cues = [cue(1, 2)]
    warp = R.Warp([anchor(0, -10.0)])
    out = R.apply_warp(cues, warp)
    assert out[0]['start_s'] >= 0.0


# ── diagnostics ─────────────────────────────────────────────────────────
def test_holdout_error_small_for_consistent_anchors():
    anchors = [anchor(i * 30, 1.0) for i in range(10)]
    err = R.holdout_error(anchors)
    assert err['n'] > 0
    assert err['p90'] < 0.01


def test_holdout_error_flags_inconsistent_anchor():
    anchors = [anchor(i * 30, 1.0) for i in range(9)]
    anchors.append(anchor(270, 12.0))
    err = R.holdout_error(anchors)
    assert err['max'] > 1.0


def test_local_medians_reports_time_buckets():
    anchors = [anchor(t, 2.0) for t in (0, 30, 60, 200, 230, 260)]
    buckets = R.local_medians(anchors, bucket=180.0)
    assert [b['t'] for b in buckets] == [0.0, 180.0]
    assert all(b['median'] == 2.0 for b in buckets)


# ── LLM driver (injected chat) ──────────────────────────────────────────
def test_align_with_llm_uses_injected_chat():
    cues = [cue(10, 12, 'hello'), cue(20, 22, 'bye')]
    rows = [cue(10.5, 12, 'こんにちは'), cue(20.5, 22, 'さようなら')]
    calls = []

    def fake_chat(messages):
        calls.append(messages)
        return '[{"a": 0, "b": 0}, {"a": 1, "b": 1}]'

    anchors = R.align_with_llm(cues, rows, window=10, chat=fake_chat, verbose=False)
    assert len(anchors) == 2
    assert len(calls) == 1
    assert 'hello' in calls[0][1]['content']


def test_build_prompt_contains_all_ids():
    cues = [cue(1, 2, 'a'), cue(3, 4, 'b')]
    rows = [cue(1.5, 2, 'あ')]
    prompt = R.build_prompt(cues, rows, [0, 1], [0])
    assert '0: [1.0] a' in prompt
    assert '1: [3.0] b' in prompt
    assert '0: [1.5] あ' in prompt


def test_align_with_llm_raises_on_empty_reply():
    cues = [cue(1, 2, 'a')]
    rows = [cue(1, 2, 'あ')]
    with pytest.raises(RuntimeError):
        R.align_with_llm(cues, rows, chat=lambda m: '')


# ── CLI end-to-end ──────────────────────────────────────────────────────
SRT = (
    "1\n00:00:10,000 --> 00:00:12,000\n一\n\n"
    "2\n00:00:20,000 --> 00:00:22,000\n二\n\n"
    "3\n00:00:30,000 --> 00:00:32,000\n三\n\n"
)


def write_srt(path, text):
    path.write_text(text, encoding='utf-8')
    return path


def anchors_file(tmp_path, cues_total=3, delta=2.0):
    anchors = [{'cue': i, 'asr': i, 'cue_start': 10 + i * 10.0,
                'asr_start': 10 + i * 10.0 + delta,
                'asr_end': 12 + i * 10.0 + delta, 'asr_len': 2.0,
                'delta': delta} for i in range(cues_total)]
    path = tmp_path / 'anchors.json'
    path.write_text(json.dumps(anchors), encoding='utf-8')
    return path


def test_cli_writes_shifted_copy_without_touching_text(tmp_path):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)
    out = tmp_path / 'out.srt'
    anchors = anchors_file(tmp_path)
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--target-srt', str(src), '--anchors', str(anchors),
                   '--output', str(out), '--report', str(tmp_path / 'r.json')])
    assert code == 0
    from lib.subtitle_io import read_subtitles
    before = read_subtitles(str(src))
    after = read_subtitles(str(out))
    assert [c['text'] for c in before] == [c['text'] for c in after]
    assert after[0]['start_s'] == pytest.approx(12.0, abs=0.01)


def test_cli_dry_run_writes_nothing(tmp_path):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)
    out = tmp_path / 'out.srt'
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--anchors', str(anchors_file(tmp_path)),
                   '--output', str(out), '--dry-run'])
    assert code == 0
    assert not out.exists()


def test_cli_refuses_low_coverage(tmp_path):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)
    anchors = anchors_file(tmp_path, cues_total=1)
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--anchors', str(anchors), '--output', str(tmp_path / 'o.srt')])
    assert code == 4


def test_cli_refuses_missing_inputs(tmp_path):
    code = R.main(['--source-srt', str(tmp_path / 'nope.srt'),
                   '--asr-srt', str(tmp_path / 'nope2.srt'),
                   '--output', str(tmp_path / 'o.srt'), '--dry-run'])
    assert code == 2


def test_cli_refuses_cue_count_mismatch(tmp_path):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)
    target = write_srt(tmp_path / 'zh.srt',
                       "1\n00:00:10,000 --> 00:00:12,000\n一\n\n")
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--target-srt', str(target), '--anchors',
                   str(anchors_file(tmp_path)), '--output', str(tmp_path / 'o.srt')])
    assert code == 4


def test_cli_refuses_to_overwrite(tmp_path):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)
    out = write_srt(tmp_path / 'o.srt', SRT)
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--anchors', str(anchors_file(tmp_path)), '--output', str(out)])
    assert code == 2


def test_cli_llm_failure_exit_code(tmp_path, monkeypatch):
    src = write_srt(tmp_path / 'en.srt', SRT)
    asr = write_srt(tmp_path / 'asr.srt', SRT)

    def boom(*args, **kwargs):
        raise RuntimeError('no api key')

    monkeypatch.setattr(R, 'align_with_llm', boom)
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--output', str(tmp_path / 'o.srt')])
    assert code == 3


# ── ground-truth recovery ───────────────────────────────────────────────
def srt_block(index, start, end, text):
    def fmt(t):
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        return f'{h:02d}:{m:02d}:{s:02d},000'

    return f'{index}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n'


def identity_chat(messages):
    """Fake LLM: pair A/B lines that share the same 'line_N' text."""
    import re
    prompt = messages[-1]['content']
    a_part, _, b_part = prompt.partition('\nB:\n')
    a = dict(re.findall(r'^(\d+): \[[\d.]+\] (line_\d+)$', a_part, re.M))
    b = dict((text, idx) for idx, text in
             re.findall(r'^(\d+): \[[\d.]+\] (line_\d+)$', b_part, re.M))
    items = [{'a': int(idx), 'b': int(b[text])} for idx, text in a.items()
             if text in b]
    return json.dumps(items)


def test_recovers_piecewise_offsets_end_to_end(tmp_path, monkeypatch):
    """Cues shifted by +4/0/+6 s vs the ASR must come back aligned to the ASR."""
    n = 60
    offsets = [4.0 if i < 20 else (0.0 if i < 40 else 6.0) for i in range(n)]
    cue_srt, asr_srt = [], []
    for i in range(n):
        cue_srt.append(srt_block(i + 1, i * 10, i * 10 + 3, f'line_{i}'))
        asr_srt.append(srt_block(i + 1, i * 10 + offsets[i],
                                 i * 10 + offsets[i] + 3, f'line_{i}'))
    src = write_srt(tmp_path / 'en.srt', ''.join(cue_srt))
    asr = write_srt(tmp_path / 'asr.srt', ''.join(asr_srt))
    out = tmp_path / 'out.srt'

    calls = []

    def chat(messages):
        calls.append(messages)
        return identity_chat(messages)

    monkeypatch.setattr(R, 'align_with_llm',
                        lambda cues, rows, **kw: R.pairs_to_anchors(
                            R.parse_pairs(identity_chat(
                                [{'role': 'user', 'content': R.build_prompt(
                                    cues, rows, list(range(len(cues))),
                                    list(range(len(rows))))}]),
                                list(range(len(cues))), list(range(len(rows)))),
                            cues, rows))
    code = R.main(['--source-srt', str(src), '--asr-srt', str(asr),
                   '--output', str(out), '--report', str(tmp_path / 'r.json')])
    assert code == 0

    from lib.subtitle_io import read_subtitles
    before = read_subtitles(str(src))
    after = read_subtitles(str(out))
    assert [c['text'] for c in before] == [c['text'] for c in after]
    recovered = [round(c['start_s'] - (i * 10), 1) for i, c in enumerate(after)]
    expected = offsets
    assert recovered[:20] == pytest.approx(expected[:20], abs=0.2)
    assert recovered[40:] == pytest.approx(expected[40:], abs=0.2)
    assert recovered[25] == pytest.approx(0.0, abs=0.2)


def test_baseline_auto_keeps_small_lead(monkeypatch):
    """A uniform -0.3 s lead is a subtitle convention, not an offset to fix."""
    anchors = [anchor(i * 30, -0.3) for i in range(8)]
    clean = R.prepare_anchors(anchors, baseline='auto')
    assert all(a['delta'] == pytest.approx(0.0) for a in clean)
    assert clean[0]['baseline'] == pytest.approx(-0.3)


def test_baseline_auto_keeps_real_global_offset():
    anchors = [anchor(i * 30, 2.5) for i in range(8)]
    clean = R.prepare_anchors(anchors, baseline='auto')
    assert all(a['delta'] == pytest.approx(2.5) for a in clean)


# ── encoding robustness ─────────────────────────────────────────────────
def test_read_subtitles_robust_survives_broken_utf8(tmp_path):
    """whisper.cpp can split a glyph; cp1251 fallback would mojibake the file."""
    good = "1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n\n".encode('utf-8')
    broken = "2\n00:00:03,000 --> 00:00:04,000\n".encode('utf-8') + b'\xe3\x81' + \
        "です\n\n".encode('utf-8')
    path = tmp_path / 'asr.srt'
    path.write_bytes(good + broken)
    cues = R.read_subtitles_robust(path)
    assert len(cues) == 2
    assert cues[0]['text'] == 'こんにちは'
    assert 'です' in cues[1]['text']


def test_read_subtitles_robust_keeps_crlf(tmp_path):
    path = tmp_path / 'clean.srt'
    path.write_bytes("1\r\n00:00:01,000 --> 00:00:02,000\r\nテスト\r\n\r\n".encode('utf-8'))
    cues = R.read_subtitles_robust(path)
    assert len(cues) == 1 and cues[0]['text'] == 'テスト'
