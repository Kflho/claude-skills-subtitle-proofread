"""AI-adjudicated ASR name repair: proposals -> context review -> approved output."""

import json

from lib.asr.namefix import apply_repairs, load_name_entries, propose_repairs
from lib.review.context_agent import review_name_repairs
from nouns import context_review

GLOSSARY = {'サブ': '佐武', 'イチ': '市', 'サブトイチ': '佐武与市',
            'サブヤン': '佐武'}
META = {
    'readings': {'サブ': ['サブ'], 'イチ': ['イチ'],
                 'サブトイチ': ['サブトイチ'], 'サブヤン': ['サブヤン']},
    'canonical_ja': {'サブ': '佐武', 'イチ': '市',
                     'サブトイチ': '佐武と市', 'サブヤン': '佐武'},
}


def _entries():
    return load_name_entries(GLOSSARY, META)


def _chat(decisions):
    def chat(messages):
        return json.dumps({'decisions': decisions}, ensure_ascii=False), ''
    return chat


def test_proposals_carry_context_and_are_not_applied():
    text = '闇を切り裂く男…世のサブト位置'
    proposals = propose_repairs(text, _entries())

    assert len(proposals) == 1
    hit = proposals[0]
    assert hit['from'] == 'サブト位置' and hit['to'] == '佐武と市'
    assert hit['distance'] == 0
    assert hit['context'] == text
    # the proposal is data; the line is untouched until apply_repairs
    assert apply_repairs(text, proposals).endswith('世の佐武と市')


def test_near_miss_proposals_are_flagged_as_distance_1():
    proposals = propose_repairs('サブヤモンが来た', _entries(), near_miss=True)
    assert proposals and proposals[0]['distance'] == 1
    assert proposals[0]['to'] == '佐武'      # サブヤモン ≈ サブヤン


def test_review_name_repairs_maps_verdicts():
    items = [
        {'episode': 'EP001', 'cue': 1, 'from': 'サブト位置', 'to': '佐武と市',
         'reading': 'サブトイチ', 'distance': 0, 'context': '...サブト位置'},
        {'episode': 'EP001', 'cue': 2, 'from': 'サブ', 'to': '佐武',
         'reading': 'サブ', 'distance': 0, 'context': 'サブタイトル'},
    ]
    chat = _chat([
        {'id': 0, 'verdict': 'accept', 'reason': '旁白固定句'},
        {'id': 1, 'verdict': 'reject', 'reason': '这是 subtitle 的外来语'},
        {'id': 9, 'verdict': 'accept'},
        {'id': 0, 'verdict': 'maybe'},
    ])
    decisions = review_name_repairs(items, 'brief', chat=chat)

    assert [d['verdict'] for d in decisions] == ['accept', 'reject']
    assert decisions[1]['reason'].startswith('这是 subtitle')


def test_cli_namefix_writes_only_when_out_dir_given(tmp_path):
    ja = tmp_path / 'ja'
    ja.mkdir()
    (ja / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n闇を切り裂く男…世のサブト位置\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nサブタイトルを表示\n\n",
        encoding='utf-8')
    glossary = tmp_path / 'g.json'
    glossary.write_text(json.dumps(GLOSSARY, ensure_ascii=False),
                        encoding='utf-8')
    meta = tmp_path / 'm.json'
    meta.write_text(json.dumps(META, ensure_ascii=False), encoding='utf-8')

    # dry run: no output dir → nothing written
    report = tmp_path / 'r.json'
    rc = context_review.main([
        '--project', str(tmp_path), '--glossary', str(glossary),
        '--meta', str(meta), '--ja-dir', str(ja), '--mode', 'namefix',
        '--dry-run', '--report', str(report)])
    assert rc == 0
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['proposals'] >= 1
    assert data['written'] == []
    assert (ja / 'EP001.srt').read_text(encoding='utf-8').count('サブト位置') == 1


def test_cli_namefix_writes_approved_copies(tmp_path, monkeypatch):
    ja = tmp_path / 'ja'
    ja.mkdir()
    (ja / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n闇を切り裂く男…世のサブト位置\n\n",
        encoding='utf-8')
    glossary = tmp_path / 'g.json'
    glossary.write_text(json.dumps(GLOSSARY, ensure_ascii=False),
                        encoding='utf-8')
    meta = tmp_path / 'm.json'
    meta.write_text(json.dumps(META, ensure_ascii=False), encoding='utf-8')

    def fake_review(items, brief, **kwargs):
        return [dict(items[0], verdict='accept', reason='固定句')]

    monkeypatch.setattr('lib.review.context_agent.review_name_repairs',
                        fake_review)
    out = tmp_path / 'fixed'
    rc = context_review.main([
        '--project', str(tmp_path), '--glossary', str(glossary),
        '--meta', str(meta), '--ja-dir', str(ja), '--mode', 'namefix',
        '--out-dir', str(out), '--report', str(tmp_path / 'r.json')])
    assert rc == 0
    fixed = (out / 'EP001.srt').read_text(encoding='utf-8')
    assert '佐武と市' in fixed
    # the source directory is untouched
    assert 'サブト位置' in (ja / 'EP001.srt').read_text(encoding='utf-8')

def test_cli_namefix_in_place_backs_up_source(tmp_path, monkeypatch):
    ja = tmp_path / 'ja'
    ja.mkdir()
    (ja / 'EP001.srt').write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n闇を切り裂く男…世のサブト位置\n\n",
        encoding='utf-8')
    glossary = tmp_path / 'g.json'
    glossary.write_text(json.dumps(GLOSSARY, ensure_ascii=False),
                        encoding='utf-8')
    meta = tmp_path / 'm.json'
    meta.write_text(json.dumps(META, ensure_ascii=False), encoding='utf-8')

    def fake_review(items, brief, **kwargs):
        return [dict(items[0], verdict='accept', reason='固定句')]

    monkeypatch.setattr('lib.review.context_agent.review_name_repairs',
                        fake_review)
    rc = context_review.main([
        '--project', str(tmp_path), '--glossary', str(glossary),
        '--meta', str(meta), '--ja-dir', str(ja), '--mode', 'namefix',
        '--in-place', '--report', str(tmp_path / 'r.json')])
    assert rc == 0
    assert '佐武と市' in (ja / 'EP001.srt').read_text(encoding='utf-8')
    assert 'サブト位置' in (ja / 'EP001.srt.bak').read_text(encoding='utf-8')

def test_honorific_suffix_is_preserved_in_proposals():
    proposals = propose_repairs('サブ君は', _entries())
    assert len(proposals) == 1
    assert proposals[0]['from'] == 'サブ君'
    assert proposals[0]['to'] == '佐武君'

def test_split_honorific_handles_kun_readings():
    from lib.asr.namefix import split_honorific
    assert split_honorific('サブ君', 'サブクン') == ('君', 'サブ')
    assert split_honorific('サブ君', 'サブ君') == ('君', 'サブ')
    assert split_honorific('サブちゃん', 'サブチャン') == ('ちゃん', 'サブ')
    assert split_honorific('サブ', 'サブ') == ('', 'サブ')
