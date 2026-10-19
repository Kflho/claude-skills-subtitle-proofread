"""End-to-end: normalize_variants CLI actually fixes a subtitle run.

This is the regression that keeps the claim honest: before the tool the same
episode fails the gate, after it the gate passes, unrelated text is untouched,
and a second run is a no-op.
"""

import json

from eval import text_gate
from nouns import normalize_variants

GLOSSARY = {'サブ': '佐武', 'サブトイチ': '佐武与市', 'イチ': '市'}
META = {'aliases': {'佐武': ['サブヤン', '沙布'], '市': ['伊一'],
                    '佐武与市': ['沙布托伊奇']}}

DIRTY = (
    "1\n00:00:01,000 --> 00:00:03,000\nサブヤン来了\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n沙布托伊奇在等你\n\n"
    "3\n00:00:07,000 --> 00:00:09,000\n伊一马上到\n\n"
    "4\n00:00:10,000 --> 00:00:12,000\n市场上很热闹\n\n"
)
CLEAN = ("1\n00:00:01,000 --> 00:00:03,000\n佐武来了\n\n"
         "2\n00:00:04,000 --> 00:00:06,000\n佐武与市在等你\n\n"
         "3\n00:00:07,000 --> 00:00:09,000\n市马上到\n\n"
         "4\n00:00:10,000 --> 00:00:12,000\n市场上很热闹\n\n")


def _setup(tmp_path):
    zh = tmp_path / 'zh'
    zh.mkdir()
    (zh / 'EP001.srt').write_text(DIRTY, encoding='utf-8')
    (zh / 'EP002.srt').write_text(CLEAN, encoding='utf-8')   # already clean
    g = tmp_path / 'g.json'
    g.write_text(json.dumps(GLOSSARY, ensure_ascii=False), encoding='utf-8')
    m = tmp_path / 'm.json'
    m.write_text(json.dumps(META, ensure_ascii=False), encoding='utf-8')
    return zh, str(g), str(m)


def test_dry_run_previews_without_writing(tmp_path):
    zh, g, m = _setup(tmp_path)
    report = tmp_path / 'report.json'
    rc = normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                                  '--meta', m, '--dry-run',
                                  '--report', str(report)])
    assert rc == 0
    assert (zh / 'EP001.srt').read_text(encoding='utf-8') == DIRTY
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['applied'] is False
    assert len(data['files']) == 1                    # only the dirty file
    assert len(data['files'][0]['changes']) == 3


def test_apply_fixes_the_run_and_gate_passes(tmp_path):
    zh, g, m = _setup(tmp_path)

    assert text_gate.main(['--zh-dir', str(zh), '--glossary', g, '--meta', m]) == 1

    rc = normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                                  '--meta', m, '--apply'])
    assert rc == 0

    fixed = (zh / 'EP001.srt').read_text(encoding='utf-8')
    assert '佐武来了' in fixed
    assert '佐武与市在等你' in fixed
    assert '市马上到' in fixed
    assert 'サブヤン' not in fixed and '沙布' not in fixed and '伊一' not in fixed
    # unrelated text is byte-identical
    assert '市场上很热闹' in fixed
    assert (zh / 'EP001.srt.bak').exists()

    assert text_gate.main(['--zh-dir', str(zh), '--glossary', g, '--meta', m]) == 0


def test_second_apply_is_a_noop(tmp_path):
    zh, g, m = _setup(tmp_path)
    normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                             '--meta', m, '--apply'])
    before = (zh / 'EP001.srt').read_text(encoding='utf-8')

    report = tmp_path / 'r2.json'
    assert normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                                    '--meta', m, '--apply',
                                    '--report', str(report)]) == 0
    assert (zh / 'EP001.srt').read_text(encoding='utf-8') == before
    assert json.loads(report.read_text(encoding='utf-8'))['files'] == []


def test_discover_lists_candidates_and_writes_nothing(tmp_path):
    zh, g, m = _setup(tmp_path)
    ja = tmp_path / 'ja'
    ja.mkdir()
    (ja / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nサブが来た\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nサブは強い\n\n",
        encoding='utf-8')
    (zh / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n小萨来了\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n小萨很强\n\n",
        encoding='utf-8')
    before = (zh / 'EP001.srt').read_text(encoding='utf-8')

    report = tmp_path / 'cand.json'
    rc = normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                                  '--meta', m, '--discover',
                                  '--ja-dir', str(ja), '--report', str(report)])
    assert rc == 0
    candidates = json.loads(report.read_text(encoding='utf-8'))['candidates']
    assert any(c['candidate'] == '小萨' and c['positive'] == 2
               for c in candidates)
    assert (zh / 'EP001.srt').read_text(encoding='utf-8') == before


def test_conflicting_flags_are_rejected(tmp_path):
    zh, g, m = _setup(tmp_path)
    rc = normalize_variants.main(['--zh-dir', str(zh), '--glossary', g,
                                  '--meta', m, '--dry-run', '--apply'])
    assert rc == 1


def test_no_variants_is_a_clean_noop(tmp_path):
    zh, _, _ = _setup(tmp_path)
    g = tmp_path / 'empty.json'
    g.write_text(json.dumps({'サブ': '佐武'}, ensure_ascii=False),
                 encoding='utf-8')
    rc = normalize_variants.main(['--zh-dir', str(zh), '--glossary', str(g)])
    assert rc == 0
