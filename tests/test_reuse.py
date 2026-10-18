"""Tests for reuse/align.py + reuse/apply.py — pairing and adoption rules."""

import json
from types import SimpleNamespace

import pytest

from reuse import align as reuse_align
from reuse import apply as reuse_apply
from reuse.common import bg, make_fold


def tgt(zh, start='00:00:01,000', fold=None):
    f = (fold or make_fold())(zh)
    return {'zh': zh, 'f': f, 'bg': bg(f), 'start': start,
            'src': 'ソース'}


def ref(text, gi=0, ep='EP001', start='00:00:01,000', start_s=1.0,
        fold=None):
    f = (fold or make_fold())(text)
    return {'text': text, 'f': f, 'bg': bg(f), 'gi': gi, 'ep': ep,
            'start': start, 'start_s': start_s}


# ═══════════════════════════════════════════════════════════════
# apply.judge — R1..R5
# ═══════════════════════════════════════════════════════════════

def _judge(tgt_text, ref_text, *, min_sim=0.12, min_len=4, ratio=0.58,
           never_swap=(), fold=None):
    return reuse_apply.judge(
        0, [tgt(tgt_text, fold=fold)], [ref(ref_text, fold=fold)], 0,
        min_sim, min_len, ratio, list(never_swap))


def test_judge_r2_identical_and_punctuation():
    assert _judge('你好世界', '你好世界')[0] is False
    assert _judge('你好世界', '你好世界')[1] == 'R2完全相同'
    assert _judge('你好，世界', '你好世界')[1] == 'R2仅标点差'


def test_judge_r1_short_target():
    ok, why, _, _ = _judge('好', '好啊')
    assert ok is False and why == 'R1短句'


def test_judge_r3_fragment_reference():
    ok, why, _, _ = _judge('这是一个很长的句子', '很短')
    assert ok is False and why.startswith('R3片段')


def test_judge_r5_never_swap():
    ok, why, _, _ = _judge('小瓦尔来了', '瓦尔古蕾来了',
                           never_swap=[('小瓦尔', '瓦尔古蕾')])
    assert ok is False and why.startswith('R5写法不符')


def test_judge_r4_low_similarity():
    ok, why, _, _ = _judge('完全不同的内容甲', '毫无关系的其它乙')
    assert ok is False and why.startswith('R4相似')


def test_judge_accepts_similar_text():
    ok, why, a, b = _judge('我喜欢这部动画', '我很喜欢这部动画')
    assert ok is True
    assert why.startswith('sim=')
    assert (a, b) == (0, 0)


def test_judge_name_only_bypasses_length_rules():
    fold = make_fold([['瓦尔古蕾', '瓦尔Q蕾']])
    ok, why, _, _ = _judge('我是瓦尔古蕾', '我是瓦尔Q蕾', min_len=50,
                           fold=fold)
    assert ok is True
    assert why == '形名'


# ═══════════════════════════════════════════════════════════════
# align.ask_verify — LLM answer parsing
# ═══════════════════════════════════════════════════════════════

def test_ask_verify_parses_json(monkeypatch):
    monkeypatch.setattr(reuse_align, 'call_chat',
                        lambda *a, **k: '{"1": "yes", "2": "no"}')

    out = reuse_align.ask_verify([('a', 'b'), ('c', 'd')], 'm', '')

    assert out == [True, False]


def test_ask_verify_accepts_fullwidth_punctuation(monkeypatch):
    monkeypatch.setattr(reuse_align, 'call_chat',
                        lambda *a, **k: '{"1"："yes"，"2"："NO"}')
    assert reuse_align.ask_verify([('a', 'b'), ('c', 'd')], 'm',
                                  '') == [True, False]


def test_ask_verify_missing_answer_is_none(monkeypatch):
    monkeypatch.setattr(reuse_align, 'call_chat',
                        lambda *a, **k: '{"1": "yes"}')
    assert reuse_align.ask_verify([('a', 'b'), ('c', 'd')], 'm',
                                  '') == [True, None]


@pytest.mark.parametrize('response', [None, '', 'not json'])
def test_ask_verify_unusable_response(monkeypatch, response):
    monkeypatch.setattr(reuse_align, 'call_chat', lambda *a, **k: response)
    assert reuse_align.ask_verify([('a', 'b')], 'm', '') == [None]


# ═══════════════════════════════════════════════════════════════
# align.anchor_chain
# ═══════════════════════════════════════════════════════════════

def _pair_entries(texts):
    targets = [tgt(t) for t in texts]
    references = [ref(t, gi=i) for i, t in enumerate(texts)]
    return targets, references


def test_anchor_chain_is_monotonic():
    targets, references = _pair_entries(['甲乙丙', '丁戊己', '庚辛壬'])

    chain = reuse_align.anchor_chain(targets, references, thr=0.6,
                                     max_back=80)

    assert chain == [(0, 0), (1, 1), (2, 2)]


def test_anchor_chain_empty_or_strict():
    targets, references = _pair_entries(['甲乙丙'])
    assert reuse_align.anchor_chain([], references, 0.6, 80) == []
    assert reuse_align.anchor_chain(targets, references, thr=1.01,
                                    max_back=80) == []


def test_anchor_chain_skips_unrelated_lines():
    targets, _ = _pair_entries(['甲乙丙', '丁戊己'])
    references = [ref('完全无关的内容', gi=0)]
    assert reuse_align.anchor_chain(targets, references, 0.6, 80) == []


# ═══════════════════════════════════════════════════════════════
# align.pair_up
# ═══════════════════════════════════════════════════════════════

def _args(tmp_path, **overrides):
    base = dict(anchor_thr=0.6, anchor_back=80, topk=10, sim_floor=0.12,
                out_dir=str(tmp_path), groups=[], batch=10, workers=1,
                model='model')
    base.update(overrides)
    return SimpleNamespace(**base)


def test_pair_up_writes_verify_cache_and_matches(tmp_path, monkeypatch):
    targets, references = _pair_entries(['甲乙丙', '丁戊己'])
    monkeypatch.setattr(reuse_align, 'LLM_API_KEY', 'key')
    monkeypatch.setattr(reuse_align, 'ask_verify',
                        lambda pairs, model, hints: [True] * len(pairs))

    recs = reuse_align.pair_up('SP01', targets, references,
                               _args(tmp_path), make_fold())

    assert set(recs) == {'0', '1'}
    assert recs['0']['gi'] == 0
    assert recs['0']['src'] == 'ソース'
    cache = json.loads((tmp_path / 'verify_SP01.json').read_text(
        encoding='utf-8'))
    assert cache['0:0'] is True


def test_pair_up_reuses_cache_without_llm(tmp_path, monkeypatch):
    targets, references = _pair_entries(['甲乙丙'])
    monkeypatch.setattr(reuse_align, 'LLM_API_KEY', 'key')
    (tmp_path / 'verify_SP01.json').write_text(
        json.dumps({'0:0': True}), encoding='utf-8')

    def must_not_call(*args, **kwargs):
        raise AssertionError('cache hit must skip LLM')

    monkeypatch.setattr(reuse_align, 'ask_verify', must_not_call)

    recs = reuse_align.pair_up('SP01', targets, references,
                               _args(tmp_path), make_fold())
    assert set(recs) == {'0'}


def test_pair_up_requires_api_key_when_todo(tmp_path, monkeypatch):
    targets, references = _pair_entries(['甲乙丙'])
    monkeypatch.setattr(reuse_align, 'LLM_API_KEY', '')

    with pytest.raises(SystemExit) as exc:
        reuse_align.pair_up('SP01', targets, references, _args(tmp_path),
                            make_fold())
    assert 'LLM_API_KEY' in str(exc.value)


def test_pair_up_aborts_when_all_checks_fail(tmp_path, monkeypatch):
    targets, references = _pair_entries(['甲乙丙'])
    monkeypatch.setattr(reuse_align, 'LLM_API_KEY', 'key')
    monkeypatch.setattr(reuse_align, 'ask_verify',
                        lambda pairs, model, hints: [None] * len(pairs))

    with pytest.raises(SystemExit) as exc:
        reuse_align.pair_up('SP01', targets, references, _args(tmp_path),
                            make_fold())
    assert '全部失败' in str(exc.value)


# ═══════════════════════════════════════════════════════════════
# apply.run
# ═══════════════════════════════════════════════════════════════

def _write_srt(path, texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> '
                      f'00:00:{i * 2 + 1:02d},000\n{text}\n')
    path.write_text('\n'.join(blocks), encoding='utf-8')


def _run_args(target_dir, out_dir, **overrides):
    base = dict(target_dir=str(target_dir), source_dir='unused',
                out_dir=str(out_dir), min_sim=0.12, ratio=0.58, min_len=4,
                never_swap=[], adj_gap=4.0, max_ext=1.35, apply=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_apply_run_dry_run_writes_report_only(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    srt = target / 'SP01.srt'
    _write_srt(srt, ['我喜欢这部动画'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'matches_SP01.json').write_text(json.dumps({
        'matches': {'0': {'gi': 0}},
    }), encoding='utf-8')
    references = [ref('我很喜欢这部动画', gi=0)]
    monkeypatch.setattr(reuse_apply, 'load_target',
                        lambda *a, **k: [tgt('我喜欢这部动画')])

    reuse_apply.run('SP01', _run_args(target, out), references,
                    make_fold())

    report = (out / 'apply_SP01.txt').read_text(encoding='utf-8')
    assert '采纳 1' in report
    assert '我喜欢这部动画' in srt.read_text(encoding='utf-8')  # unchanged
    assert not (target / 'SP01.srt.bak').exists()


def test_apply_run_writes_srt_and_backup(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    srt = target / 'SP01.srt'
    _write_srt(srt, ['我喜欢这部动画'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'matches_SP01.json').write_text(json.dumps({
        'matches': {'0': {'gi': 0}},
    }), encoding='utf-8')
    references = [ref('我很喜欢这部动画', gi=0)]
    monkeypatch.setattr(reuse_apply, 'load_target',
                        lambda *a, **k: [tgt('我喜欢这部动画')])

    reuse_apply.run('SP01', _run_args(target, out, apply=True), references,
                    make_fold())

    assert '我很喜欢这部动画' in srt.read_text(encoding='utf-8')
    assert (target / 'SP01.srt.bak').exists()


def test_apply_run_rejects_r2_in_report(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    srt = target / 'SP01.srt'
    _write_srt(srt, ['我喜欢这部动画'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'matches_SP01.json').write_text(json.dumps({
        'matches': {'0': {'gi': 0}},
    }), encoding='utf-8')
    references = [ref('我喜欢这部动画', gi=0)]
    monkeypatch.setattr(reuse_apply, 'load_target',
                        lambda *a, **k: [tgt('我喜欢这部动画')])

    reuse_apply.run('SP01', _run_args(target, out, apply=True), references,
                    make_fold())

    assert '采纳 0' in (out / 'apply_SP01.txt').read_text(encoding='utf-8')
    assert not (target / 'SP01.srt.bak').exists()


def test_apply_run_requires_matches_file(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    _write_srt(target / 'SP01.srt', ['文本'])
    monkeypatch.setattr(reuse_apply, 'load_target',
                        lambda *a, **k: [tgt('文本')])
    with pytest.raises(SystemExit) as exc:
        reuse_apply.run('SP01', _run_args(target, tmp_path / 'out'), [],
                        make_fold())
    assert 'reuse/align.py' in str(exc.value)
