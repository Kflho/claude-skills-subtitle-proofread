"""Tests for reuse/align.py + reuse/apply.py — pairing and adoption rules."""

import json
import sys
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


def test_apply_run_requires_target_file(tmp_path):
    with pytest.raises(SystemExit) as exc:
        reuse_apply.run('SP01', _run_args(tmp_path, tmp_path / 'out'), [],
                        make_fold())
    assert '找不到 SP01' in str(exc.value)


def test_apply_extend_merges_consecutive_reference_lines():
    targets = [tgt('我今天去了学校和朋友玩')]
    refs = [ref('我今天去了', gi=0, start_s=1.0),
            ref('学校和朋友玩', gi=1, start_s=2.0)]

    a, b = reuse_apply.extend(
        0, 0, 0, targets, refs, set(), adj_gap=4.0, max_ext=2.0,
        fold=make_fold())

    assert (a, b) == (0, 1)


def test_apply_extend_stops_on_episode_gap_length_or_similarity():
    targets = [tgt('我今天去了学校和朋友玩')]
    base = ref('我今天去了', gi=0, start_s=1.0)

    def ext(next_ref, used=frozenset(), max_ext=2.0):
        return reuse_apply.extend(
            0, 0, 0, targets, [base, next_ref], set(used),
            adj_gap=4.0, max_ext=max_ext, fold=make_fold())

    assert ext(ref('学校和朋友玩', gi=1, ep='EP002', start_s=2.0)) == (0, 0)
    assert ext(ref('学校和朋友玩', gi=1, start_s=10.0)) == (0, 0)
    assert ext(ref('学校和朋友玩', gi=1, start_s=2.0), max_ext=0.9) == (0, 0)
    assert ext(ref('完全无关的其他内容', gi=1, start_s=2.0)) == (0, 0)
    assert ext(ref('学校和朋友玩', gi=1, start_s=2.0), used={1}) == (0, 0)


def test_apply_run_rejects_second_use_of_same_reference(tmp_path, monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    _write_srt(target / 'SP01.srt', ['我喜欢这部动画', '我喜欢这部动画'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'matches_SP01.json').write_text(json.dumps({
        'matches': {'0': {'gi': 0}, '1': {'gi': 0}},
    }), encoding='utf-8')
    references = [ref('我很喜欢这部动画', gi=0)]
    monkeypatch.setattr(
        reuse_apply, 'load_target',
        lambda *a, **k: [tgt('我喜欢这部动画'), tgt('我喜欢这部动画')])

    reuse_apply.run('SP01', _run_args(target, out), references, make_fold())

    report = (out / 'apply_SP01.txt').read_text(encoding='utf-8')
    assert '采纳 1  丢弃 1' in report
    assert 'R6与已采纳冲突' in report


def test_apply_run_rejects_conflict_introduced_by_extension(tmp_path,
                                                           monkeypatch):
    target = tmp_path / 'zh'
    target.mkdir()
    _write_srt(target / 'SP01.srt', ['我今天去了学校和朋友玩',
                                     '学校和朋友玩得开心'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'matches_SP01.json').write_text(json.dumps({
        'matches': {'0': {'gi': 0}, '1': {'gi': 1}},
    }), encoding='utf-8')
    references = [
        ref('我今天去了学校', gi=0, start_s=1.0),
        ref('和朋友玩得很快乐', gi=1, start_s=2.0),
    ]
    monkeypatch.setattr(
        reuse_apply, 'load_target',
        lambda *a, **k: [tgt('我今天去了学校和朋友玩'),
                         tgt('和朋友玩得开心')])

    reuse_apply.run('SP01', _run_args(target, out, max_ext=2.0),
                    references, make_fold())

    report = (out / 'apply_SP01.txt').read_text(encoding='utf-8')
    assert '采纳 1  丢弃 1' in report
    assert 'R6与已采纳冲突' in report
    assert '..' in report  # merged reference range is shown


# ═══════════════════════════════════════════════════════════════
# reuse/apply.py CLI
# ═══════════════════════════════════════════════════════════════

def _apply_argv(tmp_path, episodes='SP01,SP02', extra=()):
    return ['apply.py', '-e', episodes,
            '--source-dir', str(tmp_path / 'src'),
            '--target-dir', str(tmp_path / 'zh'),
            '--reference-dir', str(tmp_path / 'ref'),
            '--out-dir', str(tmp_path / 'out'), *extra]


def test_apply_main_runs_each_tag_in_dry_run(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(reuse_apply, 'load_variants', lambda p: ([], []))
    monkeypatch.setattr(reuse_apply, 'load_reference',
                        lambda *a, **k: [{'text': '参考'}])
    monkeypatch.setattr(reuse_apply, 'run',
                        lambda tag, args, ref, fold: calls.append(tag))
    monkeypatch.setattr(sys, 'argv', _apply_argv(tmp_path))

    reuse_apply.main()

    assert calls == ['SP01', 'SP02']
    assert (tmp_path / 'out').is_dir()
    assert '（dry-run）' in capsys.readouterr().out


def test_apply_main_apply_skips_dry_run_notice(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reuse_apply, 'load_variants', lambda p: ([], []))
    monkeypatch.setattr(reuse_apply, 'load_reference',
                        lambda *a, **k: [{'text': '参考'}])
    monkeypatch.setattr(reuse_apply, 'run', lambda *a, **k: None)
    monkeypatch.setattr(sys, 'argv', _apply_argv(tmp_path, extra=['--apply']))

    reuse_apply.main()

    assert '（dry-run）' not in capsys.readouterr().out


def test_apply_main_rejects_unparseable_episodes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', _apply_argv(tmp_path, episodes='???'))

    with pytest.raises(SystemExit) as exc:
        reuse_apply.main()
    assert '集号无法解析' in str(exc.value)


def test_apply_main_rejects_missing_variants_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', _apply_argv(
        tmp_path, extra=['--variants', str(tmp_path / 'nope.json')]))

    with pytest.raises(SystemExit) as exc:
        reuse_apply.main()
    assert 'variants 文件不存在' in str(exc.value)


# ═══════════════════════════════════════════════════════════════
# reuse/align.py CLI — pair every selected episode, write matches
# ═══════════════════════════════════════════════════════════════

def _align_argv(tmp_path, episodes='EP001', extra=()):
    return ['align.py', '-e', episodes,
            '--source-dir', str(tmp_path / 'src'),
            '--target-dir', str(tmp_path / 'zh'),
            '--reference-dir', str(tmp_path / 'ref'),
            '--out-dir', str(tmp_path / 'out'), *extra]


def test_align_main_writes_matches_and_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reuse_align, 'load_variants', lambda path: ([], []))
    monkeypatch.setattr(reuse_align, 'load_reference',
                        lambda *a, **k: [{'text': '参考', 'ep': 'REF01'}])
    monkeypatch.setattr(reuse_align, 'load_target',
                        lambda *a, **k: [{'zh': '我的译文', 'ja': '源'}])
    monkeypatch.setattr(
        reuse_align, 'pair_up',
        lambda tag, tgt, ref, args, fold: {
            '0': {'zh': '我的译文', 'ref_text': '参考',
                  'ref_ep': 'REF01', 'ref_start': '00:00:01.00',
                  'sim': 0.9},
        })
    report = tmp_path / 'report.txt'
    monkeypatch.setattr(sys, 'argv', _align_argv(
        tmp_path, extra=['--report', str(report)]))

    reuse_align.main()

    matches = json.loads(
        (tmp_path / 'out' / 'matches_EP001.json').read_text(encoding='utf-8'))
    assert matches['n_target'] == 1
    assert matches['n_reference'] == 1
    assert matches['matches']['0']['sim'] == 0.9
    text = report.read_text(encoding='utf-8')
    assert '参考 cue 1' in capsys.readouterr().out
    assert 'EP001: 命中 1/1' in text
    assert '[改]' in text
    assert '参考: REF01 00:00:01' in text


def test_align_main_rejects_unparseable_episodes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', _align_argv(tmp_path, episodes='???'))

    with pytest.raises(SystemExit) as exc:
        reuse_align.main()
    assert '集号无法解析' in str(exc.value)


def test_align_main_rejects_missing_variants_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', _align_argv(
        tmp_path, extra=['--variants', str(tmp_path / 'nope.json')]))

    with pytest.raises(SystemExit) as exc:
        reuse_align.main()
    assert 'variants 文件不存在' in str(exc.value)
