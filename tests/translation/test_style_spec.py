"""Style spec (source-driven keywords) and deterministic Chinese style lint."""

import pytest

from lib.context.style_spec import (
    build_style_spec, compose_hint, derive_keywords, analyze_source_style,
    MAX_HINT_CHARS,
)
from lib.context.style_lint import lint_cues, lint_text

POSITIVE = [
    ('你这家伙真离谱', 'modern'),
    ('这事儿咋整啊', 'dialect'),
    ('俺也不知道', 'dialect'),
    ('老铁，没毛病', 'modern'),
    ('我们学校明天放假', 'anachronism'),
    ('搞定这单生意', 'modern'),
    ('你真得瑟', 'dialect'),
]
NEGATIVE = [
    '残酷的现实',
    '整理行装',
    '请恕在下直言',
    '你这混蛋，滚出去',
    '将军的帅旗',
    '收拾整齐',
    '一个问题也没有',
    # decoys found in the real corpus: these must never be flagged
    '这伙盗贼跟普通的贼不一样',
    '才不只是兄弟呢',
    '兄弟如手足',
    '压力全在他一个人身上',
    '上头净找我麻烦，就因为没抓到那杀人盗贼',
]


def _cue(text):
    return {'start': '00:00:01,000', 'end': '00:00:02,000', 'text': text}


def test_lint_flags_every_positive():
    for text, expected in POSITIVE:
        hits = lint_text(text)
        assert hits, text
        assert expected in {h['category'] for h in hits}, (text, hits)


def test_lint_leaves_period_text_alone():
    for text in NEGATIVE:
        assert lint_text(text) == [], text


def test_lint_precision_and_recall_on_labelled_set():
    tp = sum(1 for text, _ in POSITIVE if lint_text(text))
    fp = sum(1 for text in NEGATIVE if lint_text(text))
    recall = tp / len(POSITIVE)
    precision = tp / max(1, tp + fp)
    assert recall >= 0.85, recall
    assert precision >= 0.85, precision


def test_lint_cues_aggregates_by_issue():
    cues = [_cue('离谱'), _cue('请恕在下直言'), _cue('俺也不知道')]
    result = lint_cues(cues, episode='EP001')
    assert len(result['flags']) == 2
    assert result['by_issue'] == {'现代语': 1, '方言': 1}
    assert result['flags'][0]['cue'] == 1


def test_source_features_detect_archaic_register():
    cues = [_cue('拙者の名は佐武にござる'), _cue('お主の話は候'),
            _cue('でござるか')]
    features = analyze_source_style(cues)
    assert features['archaic'] >= 3
    keywords = derive_keywords(features)
    assert any('古语' in k for k in keywords)


def test_source_features_detect_rough_and_plain():
    cues = [_cue('てめえ、やがるな'), _cue('ぶっ殺すぞ、こら'),
            _cue('貴様、野郎')]
    features = analyze_source_style(cues)
    assert features['rough'] >= 4
    assert features['polite_ratio'] < 0.2
    keywords = derive_keywords(features)
    assert any('粗口' in k for k in keywords)


def test_source_features_detect_polite_and_honorifics():
    cues = [_cue('ありがとうございます、佐武さん'),
            _cue('お願いします、お嬢様'),
            _cue('ございますね、先生')]
    features = analyze_source_style(cues)
    assert features['polite'] >= 3
    keywords = derive_keywords(features)
    assert any('敬语' in k for k in keywords)
    assert any('称谓' in k for k in keywords)


def test_hint_is_short_and_marked_as_reference():
    spec = build_style_spec([_cue('てめえ、やがるな')])
    hint = spec['hint']
    assert len(hint) <= MAX_HINT_CHARS
    assert hint.startswith('译制风格（参考，勿覆盖语境）')


def test_hint_trimming_keeps_prefix():
    long_keywords = ['古语腔（でござる/拙者）', '粗口（やがる/てめえ）',
                     '敬语较多', '称谓多（さん/様→先生/大人）',
                     '片假名多（注意外来语）', '短句为主']
    hint = compose_hint(long_keywords, max_chars=40)
    assert len(hint) <= 40
    assert hint.startswith('译制风格')


def test_modern_katakana_is_flagged_as_loanword_risk():
    cues = [_cue('コンピューターとテレビとロボット')]
    spec = build_style_spec(cues)
    assert spec['features']['katakana_ratio'] > 0.3
    assert any('片假名' in k for k in spec['keywords'])
