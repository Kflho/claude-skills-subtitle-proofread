"""Tests for audio-fingerprint OP/ED boundary detection (numpy only, no ffmpeg)."""

import os

import pytest

np = pytest.importorskip('numpy')

import audio.oped_boundaries as ob  # noqa: E402


def _tone(seconds, sr=ob.SR, freqs=(440, 880, 1320), amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    sig = sum(np.sin(2 * np.pi * f * t) for f in freqs)
    return (amp * sig / len(freqs)).astype(np.float32)


def test_parse_ref_ok():
    path, start, end = ob.parse_ref('C:/videos/ep01.avi@38-112')
    assert path == 'C:/videos/ep01.avi'
    assert (start, end) == (38.0, 112.0)
    assert ob.parse_ref('x.mkv@0:12.5')[1:] == (0.0, 12.5)


@pytest.mark.parametrize('bad', ['no-at-sign', 'x.avi@112-38', 'x.avi@abc'])
def test_parse_ref_rejects_bad_spec(bad):
    with pytest.raises(ValueError):
        ob.parse_ref(bad)


def test_band_features_shape_and_normalisation():
    feats = ob.band_features(_tone(3.0))
    assert feats.shape[0] == ob.BANDS
    assert feats.shape[1] == pytest.approx(1 + (3.0 * ob.SR - int(ob.WIN * ob.SR))
                                           // int(ob.HOP * ob.SR), abs=2)
    norms = np.linalg.norm(feats, axis=0)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_band_features_silence_is_zeroed():
    feats = ob.band_features(np.zeros(ob.SR, dtype=np.float32))
    assert np.all(feats == 0.0)


def test_locate_finds_known_offset():
    ref_audio = _tone(6.0)
    rng = np.random.default_rng(0)
    episode = (0.05 * rng.standard_normal(20 * ob.SR)).astype(np.float32)
    at = 7.0
    i0 = int(at * ob.SR)
    episode[i0:i0 + len(ref_audio)] += ref_audio
    ref = ob.band_features(ref_audio)
    feats = ob.band_features(episode)
    hit = ob.locate(ref, feats, search=(0.0, 20.0), min_score=0.5)
    assert hit is not None
    assert hit['start'] == pytest.approx(at, abs=0.15)
    assert hit['end'] - hit['start'] == pytest.approx(6.0, abs=0.2)
    # 叠了噪声还能有 0.7+；真实片源是同一份母带，同段分数更高
    assert hit['score'] > 0.7


def test_locate_reports_null_when_absent():
    ref = ob.band_features(_tone(6.0, freqs=(300, 600, 900)))
    rng = np.random.default_rng(1)
    other = ob.band_features(_tone(6.0, freqs=(2000, 2500, 3000)))
    noise = (0.05 * rng.standard_normal(10 * ob.SR)).astype(np.float32)
    episode = np.concatenate([noise, other[0] * 0 + 0])
    feats = ob.band_features(np.concatenate([noise, _tone(6.0, freqs=(2100, 2600, 3100))]))
    hit = ob.locate(ref, feats, search=(0.0, 20.0), min_score=0.55)
    assert hit is None


def test_locate_respects_search_window():
    ref_audio = _tone(4.0)
    episode = np.concatenate([
        np.zeros(3 * ob.SR, dtype=np.float32) + ref_audio[:3 * ob.SR] * 0.0,
        _tone(4.0, freqs=(500, 1000, 1500)),
    ])
    ref = ob.band_features(ref_audio)
    feats = ob.band_features(episode)
    hit = ob.locate(ref, feats, search=(0.0, 2.0), min_score=0.0)
    assert hit is not None and hit['start'] < 2.5


def test_ep_number_from_broadcast_filename():
    assert ob.ep_number('赤き血のイレブン 第01話 「このボールの下に集まれ!」 (DVD 640x480 XviD).avi') == 1
    assert ob.ep_number('EP052.mkv') == 52
    assert ob.ep_number('theme_song.mp3') is None


def test_list_videos_filters_episodes(tmp_path):
    for name in ('show 第01話 A.avi', 'show 第02話 B.avi', 'notes.txt'):
        (tmp_path / name).write_bytes(b'')
    got = ob.list_videos(str(tmp_path), [2])
    assert [n for n, _ in got] == [2]
    assert os.path.basename(got[0][1]).endswith('B.avi')
