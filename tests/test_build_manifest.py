"""Tests for the full-series manifest builder."""

from eval.build_manifest import build


def test_build_maps_videos_cc_and_split(tmp_path):
    video_dir = tmp_path / 'videos'
    cc_dir = tmp_path / 'cc'
    video_dir.mkdir()
    cc_dir.mkdir()
    for num, name in [(1, '01、漂流？冒険の島！.mkv'),
                      (2, '02、爆裂進化！グレイモン.mkv'),
                      (3, '03、蒼き狼！ガルルモン.mkv')]:
        (video_dir / name).write_bytes(b'')
        (cc_dir / f'Show.S01E{num:02d}.ja[cc].srt').write_text('x',
                                                               encoding='utf-8')

    data = build(str(video_dir), str(cc_dir), dev_count=2)

    assert [e['id'] for e in data['episodes']] == ['EP001', 'EP002', 'EP003']
    assert [e['split'] for e in data['episodes']] == ['dev', 'dev', 'test']
    assert data['episodes'][0]['main_window'] == [95, 1240]
    assert data['episodes'][1]['main_window'] == [95, 1233]
    assert data['episodes'][2]['main_window'] == [95, 1240]
    assert data['episodes'][0]['asr_json'].endswith(
        'digimon_ep001_main.qwen3.json')
