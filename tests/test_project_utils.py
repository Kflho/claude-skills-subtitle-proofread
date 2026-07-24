"""Tests for lib/project_utils.py — pure utility functions."""

from lib.project_utils import norm_ep, resources_summary, can_use_whisper


class TestNormEp:
    """Episode number normalization."""

    def test_numeric_only(self):
        assert norm_ep('64') == 'EP064'

    def test_ep_prefix(self):
        assert norm_ep('EP064') == 'EP064'

    def test_single_digit(self):
        assert norm_ep('1') == 'EP001'
        assert norm_ep('5') == 'EP005'

    def test_two_digit(self):
        assert norm_ep('12') == 'EP012'
        assert norm_ep('99') == 'EP099'

    def test_three_digit(self):
        assert norm_ep('193') == 'EP193'


class TestResourcesSummary:
    """String formatting of resources dict."""

    def test_all_available(self):
        r = {
            'has_target_subs': True,
            'has_video': True,
            'has_whisper': True,
            'has_reference': True,
            'whisper_backend': 'whisper-cpp',
        }
        s = resources_summary(r)
        assert '[+]' in s or '✅' in s
        assert 'whisper-cpp' in s

    def test_nothing_available(self):
        r = {
            'has_target_subs': False,
            'has_video': False,
            'has_whisper': False,
            'has_reference': False,
            'whisper_backend': '',
        }
        s = resources_summary(r)
        assert '[-]' in s or '✗' in s


class TestCanUseWhisper:
    """Whisper availability check."""

    def test_has_whisper(self):
        r = {'has_whisper': True, 'has_video': True}
        assert can_use_whisper(r) is True

    def test_no_whisper(self):
        r = {'has_whisper': False, 'has_video': True}
        assert can_use_whisper(r) is False

    def test_skip_whisper(self):
        r = {'has_whisper': True, 'has_video': True}
        assert can_use_whisper(r, skip_whisper=True) is False

    def test_no_video(self):
        r = {'has_whisper': True, 'has_video': False}
        assert can_use_whisper(r) is False
