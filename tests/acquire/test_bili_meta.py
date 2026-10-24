"""bili_meta: upload structure parsing, no network in the tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from acquire import bili_meta  # noqa: E402


def _payload(durations=(1501, 1501, 1471), subtitles=None):
    pages = [{'page': n + 1, 'cid': 100 + n, 'part': f'{n+1:02d}',
              'duration': d} for n, d in enumerate(durations)]
    return {
        'bvid': 'BV1xx411P7CU',
        'title': '【1080P+】赤胴铃之助 全52',
        'desc': 'nyaa\n赤胴铃之助，赤胴鈴之助',
        'owner': {'name': 'uploader'},
        'duration': sum(durations),
        'pages': pages,
        'subtitle': {'allow_submit': False, 'list': subtitles or []},
    }


def test_summarize_keeps_structure_not_the_whole_payload():
    summary = bili_meta.summarize(_payload())
    assert summary['pages'] == 3
    assert summary['duration_min'] > 0
    assert summary['page_durations'] == [1471, 1501]
    assert [item['page'] for item in summary['items']] == [1, 2, 3]


def test_empty_subtitle_list_means_asr_is_the_only_route():
    summary = bili_meta.summarize(_payload())
    assert summary['has_subtitles'] is False
    assert summary['subtitle_tracks'] == []


def test_subtitle_tracks_are_reported_with_language_and_ai_flag():
    summary = bili_meta.summarize(_payload(subtitles=[
        {'lan': 'ai-zh', 'ai_type': 1},
        {'lan': 'ja', 'ai_type': 0},
    ]))
    assert summary['has_subtitles'] is True
    assert summary['subtitle_tracks'] == [
        {'lang': 'ai-zh', 'ai': 1}, {'lang': 'ja', 'ai': 0}]


def test_odd_duration_page_is_flagged():
    """A part far from the average is usually a merged double episode."""
    summary = bili_meta.summarize(_payload((1501, 1501, 3000)))
    assert [page['page'] for page in summary['odd_pages']] == [3]


def test_uniform_pages_have_no_odd_flags():
    assert bili_meta.summarize(_payload((1501, 1501, 1501)))['odd_pages'] == []
