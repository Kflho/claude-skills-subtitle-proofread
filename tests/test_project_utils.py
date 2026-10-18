"""Tests for lib/project_utils.py — paths, detection, backup."""

import json

import pytest

from lib.project_utils import (
    backup_file, backup_files, can_use_whisper, detect_format,
    detect_project_lang, find_srt, find_video, get_target_dir, load_json,
    norm_ep, resources_summary,
)


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


# ═══════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════

class TestTargetDir:
    def test_defaults_to_ai_review_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv('INPUT_DIR', raising=False)
        monkeypatch.delenv('SUBTITLE_INPUT_DIR', raising=False)
        assert get_target_dir(str(tmp_path)).endswith('AI审查后')

    def test_honours_input_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv('INPUT_DIR', 'subs')
        assert get_target_dir(str(tmp_path)) == str(tmp_path / 'subs')

    def test_dot_means_project_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv('INPUT_DIR', '.')
        assert get_target_dir(str(tmp_path)) == str(tmp_path)


# ═══════════════════════════════════════════════════════════════
# Detection
# ═══════════════════════════════════════════════════════════════

def _project_with_srt(tmp_path, text, name='EP001.srt'):
    target = tmp_path / 'AI审查后'
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(
        f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n", encoding='utf-8')
    return tmp_path


def test_detect_project_lang_japanese(tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    project = _project_with_srt(tmp_path, 'こんにちは、元気ですか')
    assert detect_project_lang(str(project), cache=False) == 'ja'


def test_detect_project_lang_chinese(tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    project = _project_with_srt(tmp_path, '你好世界，今天天气很好')
    assert detect_project_lang(str(project), cache=False) == 'zh'


def test_detect_project_lang_russian(tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    project = _project_with_srt(tmp_path, 'Привет мир как дела')
    assert detect_project_lang(str(project), cache=False) == 'ru'


def test_detect_project_lang_missing_dir_defaults_ja(tmp_path,
                                                    monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    assert detect_project_lang(str(tmp_path), cache=False) == 'ja'


def test_detect_format_counts(tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    target = tmp_path / 'AI审查后'
    target.mkdir()
    (target / 'a.ass').write_text('', encoding='utf-8')
    (target / 'b.ass').write_text('', encoding='utf-8')
    (target / 'c.srt').write_text('', encoding='utf-8')
    info = detect_format(str(tmp_path))
    assert info['ass_count'] == 2 and info['srt_count'] == 1
    assert info['primary'] == 'ass'

    (target / 'd.srt').write_text('', encoding='utf-8')
    assert detect_format(str(tmp_path))['primary'] == 'mixed'


# ═══════════════════════════════════════════════════════════════
# File finding
# ═══════════════════════════════════════════════════════════════

def test_find_srt_by_episode_number(tmp_path, monkeypatch):
    monkeypatch.delenv('INPUT_DIR', raising=False)
    target = tmp_path / 'AI审查后'
    target.mkdir()
    (target / 'Show - 003 - Title.srt').write_text('', encoding='utf-8')
    name, path = find_srt(str(tmp_path), 'EP003')
    assert name == 'Show - 003 - Title.srt'
    assert path


def test_find_video_avoids_hash_false_positive(tmp_path):
    videos = tmp_path / 'videos'
    videos.mkdir()
    (videos / 'Show - 192 - Title.mkv').write_bytes(b'')
    (videos / 'C319227A.mkv').write_bytes(b'')

    found = find_video(str(tmp_path), 'EP192', video_dir=str(videos))
    assert found and found.endswith('Show - 192 - Title.mkv')


def test_find_video_returns_none_when_missing(tmp_path):
    assert find_video(str(tmp_path), 'EP001') is None


# ═══════════════════════════════════════════════════════════════
# Backup / JSON
# ═══════════════════════════════════════════════════════════════

def test_backup_file_never_overwrites_first_backup(tmp_path):
    path = tmp_path / 'a.srt'
    path.write_text('original', encoding='utf-8')
    bak = backup_file(str(path))
    assert bak == str(path) + '.bak'

    path.write_text('modified', encoding='utf-8')
    backup_file(str(path))
    assert open(bak, encoding='utf-8').read() == 'original'


def test_backup_file_missing_returns_none(tmp_path):
    assert backup_file(str(tmp_path / 'nope.srt')) is None


def test_backup_files_dedupes(tmp_path):
    first = tmp_path / 'a.srt'
    first.write_text('a', encoding='utf-8')
    made = backup_files([str(first), str(first)])
    assert made == [str(first) + '.bak']


def test_load_json_missing_and_invalid(tmp_path):
    assert load_json(str(tmp_path / 'nope.json')) is None
    bad = tmp_path / 'bad.json'
    bad.write_text('{oops', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        load_json(str(bad))
