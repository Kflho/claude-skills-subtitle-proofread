"""重译代价评估：缺陷率怎么量、修 vs 翻的调用量怎么算、结论怎么落。

这套阈值不是拍脑袋：Sally 旧稿（110 集 / 42k cue）实测 punctuations 22%、
条数漂移 8.9%，工具判 RETRANSLATE；而一集只有 3% 瑕疵的稿子应该判 patch。
"""

import json

import pytest

from eval import retranslate_decision as rd


def _cue(text, start=1.0):
    return {'start': f'00:00:{int(start):02d},000', 'end': '00:00:09,000',
            'start_s': start, 'end_s': start + 2, 'text': text}


def _srt(*texts):
    blocks = []
    for i, text in enumerate(texts, 1):
        blocks.append(f'{i}\n00:00:{i * 2:02d},000 --> 00:00:{i * 2 + 1:02d},000\n'
                      f'{text}\n')
    return '\n'.join(blocks)


# ═══════════════════════════════════════════════════════════════
# 单集画像
# ═══════════════════════════════════════════════════════════════

def test_score_counts_cyrillic_residue():
    score = rd.score_episode([_cue('Привет'), _cue('你好')])
    assert score['cyrillic'] == 1
    assert score['cues'] == 2


def test_score_counts_space_separated_clauses():
    score = rd.score_episode([_cue('妈妈又没空 爸爸很忙'), _cue('正常一句。')])
    assert score['cjk_space'] == 1
    # 这一行同时也没有标点，两类缺陷都算
    assert score['punct_defect'] == 2


def test_score_counts_missing_punctuation_only_on_long_lines():
    score = rd.score_episode([_cue('我想一个人离家出走了'), _cue('嗯')])
    assert score['nopunct'] == 1     # 8 字以上、没有任何标点


def test_score_counts_overlong_lines():
    score = rd.score_episode([_cue('这' * 25)])
    assert score['overlong'] == 1


def test_score_detects_echo_against_source():
    score = rd.score_episode([_cue('Привет'), _cue('你好')],
                             [_cue('Привет'), _cue('Пока')])
    assert score['echo'] == 1


def test_score_reports_cue_count_drift():
    score = rd.score_episode([_cue('一'), _cue('二')],
                             [_cue('Один'), _cue('Два'), _cue('Три'),
                              _cue('Четыре')])
    assert score['drift'] > 0.4
    assert score['src_cues'] == 4


def test_score_ignores_vector_drawing_cues():
    """`{\\p1}m 3 19 b …` 是图形，不是没翻译的字幕。"""
    cues = [_cue(r'{\p1}m 3 19 b 2 18 -2 18{\p0}'), _cue('你好')]
    score = rd.score_episode(cues, cues)
    assert score['echo'] == 0
    assert score['overlong'] == 0
    assert score['nopunct'] == 0


def test_aggregate_uses_cue_count_as_denominator():
    scores = [rd.score_episode([_cue('Привет')] * 3),
              rd.score_episode([_cue('你好')] * 7)]
    totals = rd.aggregate(scores)
    assert totals['cues'] == 10
    assert totals['ratios']['cyrillic'] == pytest.approx(0.3)


# ═══════════════════════════════════════════════════════════════
# 结论
# ═══════════════════════════════════════════════════════════════

def _totals(**kwargs):
    base = {'cues': 1000, 'src_cues': 1000, 'cyrillic': 0, 'kana': 0,
            'echo': 0, 'cjk_space': 0, 'nopunct': 0, 'overlong': 0,
            'punct_defect': 0, 'drift': 0.0,
            'ratios': {'cyrillic': 0.0, 'kana': 0.0, 'echo': 0.0,
                       'punct': 0.0, 'overlong': 0.0}}
    base.update(kwargs)
    return base


def test_cyrillic_residue_forces_retranslate():
    verdict, reasons = rd.decide(_totals(
        cyrillic=5, ratios={**_totals()['ratios'], 'cyrillic': 0.005}))
    assert verdict == 'retranslate'
    assert any('cyrillic' in r for r in reasons)


def test_echo_above_half_percent_forces_retranslate():
    verdict, reasons = rd.decide(_totals(
        ratios={**_totals()['ratios'], 'echo': 0.006}))
    assert verdict == 'retranslate'
    assert any('echo' in r for r in reasons)


def test_cue_drift_forces_retranslate():
    verdict, reasons = rd.decide(_totals(drift=0.089))
    assert verdict == 'retranslate'
    assert any('drift' in r for r in reasons)


def test_heavy_punctuation_defects_force_retranslate():
    verdict, _ = rd.decide(_totals(
        ratios={**_totals()['ratios'], 'punct': 0.30}))
    assert verdict == 'retranslate'


def test_mid_band_is_patch_not_retranslate():
    verdict, reasons = rd.decide(_totals(
        ratios={**_totals()['ratios'], 'punct': 0.10}))
    assert verdict == 'patch'
    assert any('patch band' in r for r in reasons)


def test_light_defects_keep_existing_translation():
    verdict, _ = rd.decide(_totals(
        ratios={**_totals()['ratios'], 'punct': 0.02}))
    assert verdict == 'keep'


def test_rules_are_overridable():
    rules = {'punct_ratio': 0.50, 'patch_upper': 0.40, 'patch_lower': 0.01}
    verdict, _ = rd.decide(_totals(
        ratios={**_totals()['ratios'], 'punct': 0.30}), rules=rules)
    assert verdict == 'patch'


# ═══════════════════════════════════════════════════════════════
# 成本
# ═══════════════════════════════════════════════════════════════

def test_cost_batches_and_retry_factor():
    cost = rd.estimate_cost(1000)
    # 1000 / 20 = 50 批，乘 1.35 重试系数 = 68 次调用
    assert cost['retranslate']['calls'] == 68
    assert cost['retranslate']['wall_minutes'] > 0


def test_repair_plan_scales_with_defect_count():
    cost = rd.estimate_cost(1000, defect_cues=100)
    assert cost['repair']['cues'] == 100
    assert cost['repair']['calls'] == 10          # repair_batch = 10
    assert 0 < cost['ratio'] < 0.2


def test_no_defects_means_zero_repair_calls():
    cost = rd.estimate_cost(500)
    assert cost['repair']['calls'] == 0
    assert cost['ratio'] == 0.0


def test_price_only_applies_when_configured():
    free = rd.estimate_cost(1000)
    priced = rd.estimate_cost(1000, cost={'price_in_per_mtok': 1.0,
                                          'price_out_per_mtok': 2.0})
    assert free['retranslate']['price_cny'] == 0.0
    assert priced['retranslate']['price_cny'] > 0


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def test_cli_pairs_by_episode_number_and_writes_report(tmp_path, capsys):
    zh = tmp_path / 'zh'
    src = tmp_path / 'src'
    zh.mkdir()
    src.mkdir()
    # 命名故意不同：源 001 / 译文 Ep001 —— 必须能配上
    (src / 'Mahou Tsukai Sally 001.srt').write_text(
        _srt('Привет', 'Пока'), encoding='utf-8')
    (zh / 'Mahou_Tsukai_Sally_1966_DVD_Ep001_Simu.srt').write_text(
        _srt('Привет', '你好'), encoding='utf-8')
    report = tmp_path / 'out.json'

    rc = rd.main(['--zh-dir', str(zh), '--source-dir', str(src),
                  '--report', str(report)])
    assert rc == 0
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['episodes'] == 1
    assert data['totals']['echo'] == 1        # Привет 没翻
    assert data['verdict'] in ('retranslate', 'patch', 'keep')
    assert 'retranslate' in capsys.readouterr().err


def test_cli_cost_only_skips_verdict(tmp_path):
    (tmp_path / 'EP001.srt').write_text(_srt('你好'), encoding='utf-8')
    result = rd.run(str(tmp_path), cost_only=True)
    assert result['verdict'] == 'n/a'
    assert result['cost']['retranslate']['calls'] >= 1
