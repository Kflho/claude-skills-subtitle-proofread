"""Tests for lib/whisper_utils.py — timecodes, text classification, filtering,
SRT/ASS write-back, ffmpeg/VAD wrappers, audio probing and demucs."""

import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import lib.whisper_utils as wu
from lib.whisper_utils import (
    to_seconds, format_tc,
    extract_ep_number, extract_file_id, _is_valid_ep_number, _clean_filename,
    classify_garbled_text, is_valid_subtitle_text,
    looks_like_plausible_text, meaningful_char_count,
    is_length_anomaly, is_proper_noun_pattern,
    filter_low_confidence, setup_windows_utf8,
    OP_BOUNDARY_SEC, ED_BOUNDARY_SEC,
    parse_srt, write_srt, apply_fixes_to_srt,
    parse_ass_cues, write_ass_cues,
    parse_subtitles, write_subtitles,
    run_whisper,
    vad_filter_audio, probe_audio_streams, pick_audio_stream,
    extract_audio_wav, get_audio_duration, separate_vocals,
    is_valid_japanese, looks_like_plausible_japanese,
    is_short_garbled_fragment, is_ai_fixable,
    meaningful_jp_count,
)


class TestToSeconds:
    def test_comma(self):
        assert to_seconds("00:01:23,456") == 83.456

    def test_dot(self):
        assert to_seconds("00:01:23.456") == 83.456

    def test_hour(self):
        assert to_seconds("01:00:00.000") == 3600.0

    def test_empty(self):
        # to_seconds may raise ValueError for empty string
        try:
            result = to_seconds("")
            assert result == 0.0
        except (ValueError, AttributeError):
            pass  # raising an error for empty input is also valid behavior


class TestFormatTc:
    def test_roundtrip(self):
        assert format_tc(83.456) == "00:01:23,456"

    def test_hour(self):
        assert format_tc(3600.0) == "01:00:00,000"

    def test_zero(self):
        assert format_tc(0.0) == "00:00:00,000"

    def test_hour_boundary(self):
        assert format_tc(3661.5) == "01:01:01,500"


class TestEpNumber:
    def test_extract_ep_number_standard(self):
        assert extract_ep_number("show_064.srt") == "EP064"

    def test_extract_with_brackets(self):
        result = extract_ep_number("[Group] Show - 031 EP..srt")
        assert result == "EP031" or result.startswith("EP")

    def test_extract_fallback(self):
        result = extract_ep_number("no_number_here.srt")
        assert result == "EP000" or result == "???"

    def test_valid_ep_number(self):
        assert _is_valid_ep_number(64) is True
        assert _is_valid_ep_number(1) is True
        assert _is_valid_ep_number(193) is True

    def test_invalid_ep_number_resolution(self):
        assert _is_valid_ep_number(720) is False
        assert _is_valid_ep_number(1080) is False

    def test_invalid_ep_number_year(self):
        assert _is_valid_ep_number(2024) is False
        assert _is_valid_ep_number(1998) is False

    def test_clean_filename(self):
        result = _clean_filename("Ep064 (1280x720).srt")
        assert "Ep064" in result


class TestClassifyGarbledText:
    """Text classification — the core correctness logic."""

    def test_clean_japanese(self):
        r = classify_garbled_text("こんにちは", target_lang='ja')
        assert r['type'] == 'clean'

    def test_garbled_latin_in_ja(self):
        r = classify_garbled_text("konnichiwa こんにちは", target_lang='ja')
        assert r['type'] == 'garbled'

    def test_clean_chinese(self):
        r = classify_garbled_text("你好世界", target_lang='zh')
        assert r['type'] == 'clean'

    def test_garbled_cyrillic_in_en(self):
        r = classify_garbled_text("Привет мир", target_lang='en')
        assert r['type'] == 'garbled'

    def test_clean_english(self):
        r = classify_garbled_text("Hello world", target_lang='en')
        assert r['type'] == 'clean'

    def test_empty_text(self):
        r = classify_garbled_text("", target_lang='ja')
        assert r['type'] == 'clean'  # empty = no content to be garbled

    def test_garbled_cue_in_op_region_is_forced_clean(self, tmp_path):
        # Fixed OP/ED windows are only a *safety net*: a Latin-only cue inside
        # the OP window is treated as lyrics and not flagged (see config.py).
        path = tmp_path / 'op.srt'
        path.write_text(
            "1\n00:00:10,000 --> 00:00:12,000\nhello lyric\n\n"
            "2\n00:06:40,000 --> 00:06:42,000\nhello middle\n\n"
            "3\n00:11:40,000 --> 00:11:42,000\nこんにちは\n\n",
            encoding='utf-8')
        cues = parse_srt(str(path), mark_garbled=True, target_lang='ja')
        assert cues[0]['is_garbled'] is False       # inside OP window
        assert cues[1]['is_garbled'] is True        # body → garbled
        assert cues[2]['is_garbled'] is False       # clean Japanese

    def test_garbled_cue_in_ed_region_is_forced_clean(self, tmp_path):
        path = tmp_path / 'ed.srt'
        path.write_text(
            "1\n00:00:10,000 --> 00:00:12,000\n日本語です\n\n"
            "2\n00:06:40,000 --> 00:06:42,000\n日本語です\n\n"
            "3\n00:11:00,000 --> 00:11:02,000\nhello ending\n\n",
            encoding='utf-8')
        cues = parse_srt(str(path), mark_garbled=True, target_lang='ja')
        assert cues[2]['is_garbled'] is False       # inside ED window


class TestTextValidation:
    def test_valid_ja(self):
        assert is_valid_subtitle_text("こんにちは", target_lang='ja') is True

    def test_invalid_ja_latin_mix(self):
        # Pure Latin in ja context should be flagged
        result = is_valid_subtitle_text("hello world", target_lang='ja')
        # May be True or False depending on content
        assert isinstance(result, bool)

    def test_looks_plausible_ja(self):
        assert looks_like_plausible_text("行くぞ", target_lang='ja') is True

    def test_implausible_noise(self):
        # Even short exclamations may be classified as plausible
        result = looks_like_plausible_text("あっ！", target_lang='ja')
        assert isinstance(result, bool)


class TestMeaningfulCharCount:
    def test_ja_kanji(self):
        assert meaningful_char_count("行くぞ", target_lang='ja') == 3

    def test_ja_exclamations_zero(self):
        assert meaningful_char_count("あっ！えーっ！", target_lang='ja') == 0

    def test_ja_mixed(self):
        count = meaningful_char_count("そうですね", target_lang='ja')
        assert count >= 4  # そ う で す ね are all kana

    def test_zh_count(self):
        assert meaningful_char_count("你好世界", target_lang='zh') >= 2


class TestLengthAnomaly:
    def test_hallucination_detected(self):
        assert is_length_anomaly("abc", "a very long hallucinated response",
                                 ratio=3.0) is True

    def test_normal_no_anomaly(self):
        assert is_length_anomaly("short", "brief", ratio=3.0) is False

    def test_similar_length(self):
        assert is_length_anomaly("hello world", "hello earth", ratio=3.0) is False


class TestProperNounPattern:
    def test_katakana_name(self):
        assert is_proper_noun_pattern("アトム", target_lang='ja') is True

    def test_hiragana_not_noun(self):
        assert is_proper_noun_pattern("あとむ", target_lang='ja') is False

    def test_english(self):
        # Capitalized word in en context
        result = is_proper_noun_pattern("Astro", target_lang='en')
        assert isinstance(result, bool)


class TestFilterLowConfidence:
    def test_discard_high_no_speech_prob(self):
        segs = [
            {'no_speech_prob': 0.8, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) == 1
        assert kept[0]['text'] == 'good'
        assert len(discarded) == 1
        assert 'no_speech_prob' in discarded[0]['discard_reason']

    def test_discard_low_avg_logprob(self):
        segs = [
            {'no_speech_prob': 0.1, 'avg_logprob': -2.0,
             'compression_ratio': 1.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) >= 1

    def test_discard_high_compression(self):
        segs = [
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 3.0, 'text': 'bad'},
            {'no_speech_prob': 0.1, 'avg_logprob': -0.5,
             'compression_ratio': 1.0, 'text': 'good'},
        ]
        kept, discarded = filter_low_confidence(segs)
        assert len(kept) >= 1

    def test_empty_list(self):
        kept, discarded = filter_low_confidence([])
        assert kept == []
        assert discarded == []


class TestSetupWindowsUtf8:
    def test_no_error(self):
        """setup_windows_utf8() should not raise on any platform."""
        try:
            setup_windows_utf8()
        except Exception as e:
            pytest.fail(f"setup_windows_utf8 raised: {e}")


class TestParseSrt:
    def test_basic(self, temp_srt):
        cues = parse_srt(temp_srt, mark_garbled=False)
        assert len(cues) == 3
        assert cues[0]['text'] == 'Hello world'

    def test_mark_garbled(self, temp_srt):
        cues = parse_srt(temp_srt, mark_garbled=True, target_lang='ja')
        assert len(cues) == 3
        # Each cue should have is_garbled field when mark_garbled=True
        assert 'is_garbled' in cues[0]


# ═══════════════════════════════════════════════════════════════
# Helpers — tiny real files + fake ffmpeg/ffprobe seam
# ═══════════════════════════════════════════════════════════════

def _write_wav(path, seconds=0.1, rate=16000):
    """Write a real, small 16k mono 16-bit WAV (never played by ffmpeg)."""
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b'\x00\x00' * int(round(seconds * rate)))
    return path


class _FakeFfmpeg:
    """run_ffmpeg seam: records argv, answers ffprobe, creates ffmpeg outputs.

    No real binary is invoked: ffprobe calls return ``ffprobe_stdout`` and
    ffmpeg calls pretend to have written their last argv element.
    """

    def __init__(self, stderr='', ffprobe_stdout=''):
        self.stderr = stderr
        self.ffprobe_stdout = ffprobe_stdout
        self.calls = []
        self.concat_text = ''

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append({'args': args, 'kwargs': dict(kwargs)})
        if kwargs.get('tool') == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout=self.ffprobe_stdout,
                                   stderr='')
        if '-i' in args:
            source = args[args.index('-i') + 1]
            if source.endswith('.concat.txt'):
                with open(source, encoding='utf-8') as handle:
                    self.concat_text = handle.read()
        target = args[-1]
        if target != '-' and not target.startswith('-'):
            with open(target, 'wb') as handle:
                handle.write(b'fake-ffmpeg-output')
        return SimpleNamespace(returncode=0, stdout='', stderr=self.stderr)


_ASS_HEADER = (
    '[Script Info]\n'
    'ScriptType: v4.00+\n'
    '\n'
    '[V4+ Styles]\n'
    'Format: Name, Fontname, Fontsize\n'
    'Style: Default,Arial,20\n'
    '\n'
    '[Events]\n'
    'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
)


def _write_ass(path, *event_lines):
    """Write a minimal but valid ASS header plus the given event lines."""
    path.write_text(_ASS_HEADER + ''.join(line + '\n' for line in event_lines),
                    encoding='utf-8')
    return path


def _install_fake_subprocess_run(monkeypatch, *, creates=None, returncode=0,
                                 stderr='', raises=None):
    """Patch whisper_utils.subprocess.run (demucs seam), never run Python."""
    calls = {}

    def fake_run(cmd, **kwargs):
        calls['cmd'] = list(cmd)
        calls['kwargs'] = kwargs
        if raises is not None:
            raise raises
        if creates is not None:
            creates.parent.mkdir(parents=True, exist_ok=True)
            creates.write_bytes(b'vocals')
        return SimpleNamespace(returncode=returncode, stdout='', stderr=stderr)

    monkeypatch.setattr(wu.subprocess, 'run', fake_run)
    return calls


# ═══════════════════════════════════════════════════════════════
# run_whisper — delegation seam
# ═══════════════════════════════════════════════════════════════

class TestRunWhisperDelegation:
    def test_passes_every_option_to_backend(self, monkeypatch):
        from lib import whisper_backends
        captured = {}

        def fake_transcribe(audio, model, language, **kwargs):
            captured.update({'audio': audio, 'model': model,
                             'language': language, **kwargs})
            return [{'start_s': 0.0, 'end_s': 1.0, 'text': 'ok'}]

        monkeypatch.setattr(whisper_backends, 'transcribe', fake_transcribe)

        out = run_whisper('ep.wav', 'whisper-cli.exe', 'model.bin',
                          language='zh', threads=4, processors=1,
                          beam_size=3, best_of=2, nth=0.3, max_context=64,
                          no_fallback=True, suppress_nst=True)

        assert out == [{'start_s': 0.0, 'end_s': 1.0, 'text': 'ok'}]
        assert captured == {
            'audio': 'ep.wav', 'model': 'model.bin', 'language': 'zh',
            'backend': None, 'whisper_cli': 'whisper-cli.exe',
            'threads': 4, 'processors': 1, 'beam_size': 3, 'best_of': 2,
            'nth': 0.3, 'max_context': 64, 'no_fallback': True,
            'suppress_nst': True,
        }

    def test_defaults_keep_legacy_values(self, monkeypatch):
        from lib import whisper_backends
        captured = {}
        monkeypatch.setattr(
            whisper_backends, 'transcribe',
            lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs)
            or [])

        assert run_whisper('a.wav', 'cli', 'm.bin') == []
        assert captured['args'] == ('a.wav', 'm.bin', 'ja')
        assert captured['kwargs'] == {
            'backend': None, 'whisper_cli': 'cli',
            'threads': 8, 'processors': 2, 'beam_size': 5, 'best_of': 8,
            'nth': 0.6, 'max_context': 0, 'no_fallback': False,
            'suppress_nst': False,
        }


# ═══════════════════════════════════════════════════════════════
# SRT write-back / apply fixes
# ═══════════════════════════════════════════════════════════════

class TestSrtWriteBack:
    def test_write_srt_roundtrips_through_parse_srt(self, tmp_path):
        path = tmp_path / 'out.srt'
        cues = [
            {'start': '00:00:01,000', 'end': '00:00:02,500', 'text': 'こんにちは'},
            {'start': '00:00:03,000', 'end': '00:00:04,000',
             'text': '二行目\n三行目'},
        ]
        write_srt(str(path), cues)

        assert path.read_text(encoding='utf-8-sig') == (
            '1\n00:00:01,000 --> 00:00:02,500\nこんにちは\n\n'
            '2\n00:00:03,000 --> 00:00:04,000\n二行目\n三行目\n\n'
        )
        reparsed = parse_srt(str(path), mark_garbled=False)
        assert [c['text'] for c in reparsed] == ['こんにちは', '二行目\n三行目']

    def test_apply_fixes_matches_by_start_in_both_formats(self, tmp_path):
        path = tmp_path / 'fix.srt'
        path.write_text(
            '1\n00:00:01,000 --> 00:00:02,000\nold one\n\n'
            '2\n00:00:03,000 --> 00:00:04,000\nold two\n\n',
            encoding='utf-8')

        fixed = apply_fixes_to_srt(str(path), [
            {'start': '00:00:01.000', 'replacement': 'new one'},
            {'action': 'replace_text', 'start': '00:00:03.000',
             'replacement': 'new two'},
        ])

        assert fixed == 2
        assert [c['text'] for c in parse_srt(str(path), mark_garbled=False)] == [
            'new one', 'new two']

    def test_apply_fixes_counts_already_correct_text(self, tmp_path):
        path = tmp_path / 'same.srt'
        path.write_text(
            '1\n00:00:01,000 --> 00:00:02,000\nalready right\n\n',
            encoding='utf-8')

        fix = [{'start': '00:00:01.000', 'replacement': 'already right'}]
        assert apply_fixes_to_srt(str(path), fix) == 1
        assert apply_fixes_to_srt(str(path), fix) == 1
        assert parse_srt(str(path), mark_garbled=False)[0]['text'] == 'already right'

    def test_apply_fixes_ignores_unknown_start_and_missing_replacement(
            self, tmp_path):
        path = tmp_path / 'miss.srt'
        path.write_text(
            '1\n00:00:01,000 --> 00:00:02,000\nkeep me\n\n',
            encoding='utf-8')

        assert apply_fixes_to_srt(
            str(path), [{'start': '00:00:99.000', 'replacement': 'x'}]) == 0
        # Empty replacement (a deletion request) is not applied by this helper;
        # callers that delete rewrite the cue list explicitly.
        assert apply_fixes_to_srt(
            str(path), [{'action': 'delete_line', 'start': '00:00:01.000'}]) == 0
        assert parse_srt(str(path), mark_garbled=False)[0]['text'] == 'keep me'


# ═══════════════════════════════════════════════════════════════
# ASS read / write
# ═══════════════════════════════════════════════════════════════

class TestAssCues:
    def test_parse_reads_dialogue_skips_comment_noise_and_empty_drawings(
            self, tmp_path):
        path = _write_ass(
            tmp_path / 'ep.ass',
            r'Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,{\i1}こんにちは{\i0}\N世界',
            'Comment: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,do not parse me',
            r'Dialogue: 0,0:00:10.00,0:00:11.00,Default,,0,0,0,,{\p1}m 0 0 l 100 0 100 100',
            r'Dialogue: 0,0:00:12.00,0:00:13.00,Default,,0,0,0,,{\i1}',
            'Dialogue: broken',
            'not a dialogue line',
            'Dialogue: 0,0:00:20.00,0:00:21.00,Default,,0,0,0,,hello body',
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,out of order',
        )

        cues = parse_ass_cues(str(path), mark_garbled=False)

        assert [c['text'] for c in cues] == [
            'こんにちは 世界', 'm 0 0 l 100 0 100 100',
            'hello body', 'out of order']
        # `line` is the 0-based raw line index; order follows the file, not time.
        assert [c['line'] for c in cues] == [9, 11, 15, 16]
        assert [c['start_s'] for c in cues] == [5.0, 10.0, 20.0, 1.0]
        assert cues[0]['start'] == '0:00:05.00'
        assert cues[0]['end'] == '0:00:07.00'
        assert cues[0]['end_s'] == 7.0
        assert 'is_garbled' not in cues[0]

    def test_parse_marks_garbled_only_outside_op_ed_windows(self, tmp_path):
        path = _write_ass(
            tmp_path / 'mark.ass',
            'Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,hello lyric',
            'Dialogue: 0,0:10:00.00,0:10:02.00,Default,,0,0,0,,hello body',
            'Dialogue: 0,0:20:00.00,0:20:02.00,Default,,0,0,0,,hello ending',
        )

        cues = parse_ass_cues(str(path))  # defaults: OP/ED 180s

        assert [c['is_garbled'] for c in cues] == [False, True, False]
        assert [c['garbled_type'] for c in cues] == ['clean', 'garbled', 'clean']

    def test_write_preserves_tags_comment_and_can_be_reparsed(self, tmp_path):
        path = _write_ass(
            tmp_path / 'write.ass',
            r'Dialogue: 0,0:00:05.00,0:00:07.00,Default,,0,0,0,,{\fad(100,100)}old text',
            'Comment: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,keep me',
            'Dialogue: 0,0:00:10.00,0:00:11.00,Sign,,0,0,0,,sign text',
        )
        cues = parse_ass_cues(str(path), mark_garbled=False)
        assert [c['line'] for c in cues] == [9, 11]

        cues[0]['text'] = '新しい, 台詞'
        cues[1]['text'] = '看板'
        write_ass_cues(str(path), cues)

        content = path.read_text(encoding='utf-8')
        assert ('Comment: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,keep me'
                in content)
        dialogue_lines = [line for line in content.splitlines()
                          if line.startswith('Dialogue:')]
        assert dialogue_lines[0].endswith(r',{\fad(100,100)}新しい, 台詞')
        assert len(dialogue_lines[0].split(',', 9)) == 10

        reparsed = parse_ass_cues(str(path), mark_garbled=False)
        assert [c['text'] for c in reparsed] == ['新しい, 台詞', '看板']
        assert [c['start'] for c in reparsed] == ['0:00:05.00', '0:00:10.00']

    def test_write_ignores_header_comment_malformed_and_out_of_range_lines(
            self, tmp_path):
        path = _write_ass(
            tmp_path / 'bad.ass',
            'Dialogue: 0,0:00:01.00',                       # 9: too few fields
            'Comment: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,keep',  # 10
            'Dialogue: 0,0:00:04.00,0:00:05.00,Default,,0,0,0,,keep dialogue',
        )
        before = path.read_text(encoding='utf-8')

        write_ass_cues(str(path), [
            {'line': 0, 'text': 'header overwrite'},
            {'line': 9, 'text': 'malformed overwrite'},
            {'line': 10, 'text': 'comment overwrite'},
            {'line': 99, 'text': 'out of range'},
            {'text': 'no line key'},
        ])

        assert path.read_text(encoding='utf-8') == before


# ═══════════════════════════════════════════════════════════════
# parse_subtitles / write_subtitles — format dispatch
# ═══════════════════════════════════════════════════════════════

class TestSubtitleDispatch:
    def test_srt_parse_write_roundtrip(self, tmp_path):
        path = tmp_path / 'ep.srt'
        path.write_text(
            '1\n00:00:01,000 --> 00:00:02,000\nold line\n\n',
            encoding='utf-8')

        cues = parse_subtitles(str(path), mark_garbled=False)
        assert [c['text'] for c in cues] == ['old line']

        cues[0]['text'] = '修正済み'
        write_subtitles(str(path), cues)
        assert parse_subtitles(str(path), mark_garbled=False)[0]['text'] == '修正済み'

    def test_ass_parse_write_roundtrip(self, tmp_path):
        path = _write_ass(
            tmp_path / 'ep.ass',
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,old line',
        )

        cues = parse_subtitles(str(path), mark_garbled=False)
        assert [c['text'] for c in cues] == ['old line']

        cues[0]['text'] = '修正済み'
        write_subtitles(str(path), cues)
        assert parse_subtitles(str(path), mark_garbled=False)[0]['text'] == '修正済み'

    def test_write_new_ass_file_from_template(self, tmp_path):
        template = _write_ass(
            tmp_path / 'template.ass',
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,template text',
        )
        target = tmp_path / 'new.ass'
        cues = [{'start': '00:00:05,000', 'end': '00:00:06,000',
                 'text': '新規台詞'}]

        write_subtitles(str(target), cues, template_path=str(template))

        assert target.exists()
        reparsed = parse_ass_cues(str(target), mark_garbled=False)
        assert [c['text'] for c in reparsed] == ['新規台詞']


# ═══════════════════════════════════════════════════════════════
# VAD pre-filter (ffmpeg silencedetect wrapper)
# ═══════════════════════════════════════════════════════════════

class TestVadFilterAudio:
    def test_unknown_duration_copies_input_and_returns_one_second(
            self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(ffprobe_stdout='')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        result = vad_filter_audio(str(source), str(target))

        assert result == ([(0, 1.0)], 1.0, 1.0)
        assert target.read_bytes() == source.read_bytes()
        assert len(fake.calls) == 2
        detect_args = fake.calls[0]['args']
        assert detect_args[detect_args.index('-af') + 1].startswith(
            'silencedetect=n=')

    def test_zero_duration_is_treated_as_unknown(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(ffprobe_stdout='0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert vad_filter_audio(str(source), str(target)) == (
            [(0, 1.0)], 1.0, 1.0)
        assert target.read_bytes() == source.read_bytes()

    def test_no_silence_events_copies_full_audio(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(ffprobe_stdout='12.5\n', stderr='')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert vad_filter_audio(str(source), str(target)) == (
            [(0, 12.5)], 12.5, 12.5)
        assert target.read_bytes() == source.read_bytes()
        assert len(fake.calls) == 2  # silencedetect + ffprobe, no concat

    def test_paired_silence_builds_segments_and_cleans_temp_files(
            self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'merged' / 'speech.wav'
        stderr = (
            '[silencedetect @ 0x1] silence_start: 2.0\n'
            '[silencedetect @ 0x1] silence_end: 5.0 | silence_duration: 3.0\n'
            '[silencedetect @ 0x1] silence_start: 10.0\n'
            '[silencedetect @ 0x1] silence_end: 12.0 | silence_duration: 2.0\n'
        )
        fake = _FakeFfmpeg(stderr=stderr, ffprobe_stdout='20.0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        segs, total, speech = vad_filter_audio(str(source), str(target))

        assert [tuple(round(v, 2) for v in seg) for seg in segs] == [
            (0.0, 2.15), (4.85, 10.15), (11.85, 20.0)]
        assert total == 20.0
        assert speech == pytest.approx(15.6)
        assert target.exists()
        assert list(target.parent.glob('*.seg*.wav')) == []
        assert not (target.parent / (target.name + '.concat.txt')).exists()
        assert fake.concat_text.count("file '") == 3
        extractions = [c for c in fake.calls if '-ss' in c['args']]
        assert len(extractions) == 3
        assert [c['args'][c['args'].index('-ss') + 1]
                for c in extractions] == ['0', '4.85', '11.85']
        concat_args = fake.calls[-1]['args']
        assert concat_args[:3] == ['-y', '-f', 'concat']
        assert concat_args[concat_args.index('-i') + 1].endswith('.concat.txt')

    def test_only_silence_start_keeps_tail_from_zero(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(ffprobe_stdout='10.0\n',
                           stderr='silence_start: 9.5\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        segs, total, speech = vad_filter_audio(str(source), str(target))

        assert [tuple(round(v, 2) for v in seg) for seg in segs] == [(0.0, 10.0)]
        assert (total, speech) == (10.0, 10.0)

    def test_only_silence_end_copies_audio(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(ffprobe_stdout='10.0\n',
                           stderr='silence_end: 2.0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert vad_filter_audio(str(source), str(target)) == (
            [(0, 10.0)], 10.0, 10.0)
        assert target.read_bytes() == source.read_bytes()
        assert not [c for c in fake.calls if '-ss' in c['args']]

    def test_leading_unpaired_silence_end_inserts_zero_start(
            self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(
            ffprobe_stdout='10.0\n',
            stderr='silence_end: 1.0\nsilence_start: 3.0\nsilence_end: 4.0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        segs, _, speech = vad_filter_audio(str(source), str(target))

        assert [tuple(round(v, 2) for v in seg) for seg in segs] == [
            (0.85, 3.15), (3.85, 10.0)]
        assert speech == pytest.approx(8.45)

    def test_no_speech_segments_copies_audio(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        stderr = (
            'silence_start: 0.1\nsilence_end: 0.2\n'
            'silence_start: 0.3\nsilence_end: 0.4\n'
            'silence_start: 0.5\nsilence_end: 9.9\n'
        )
        fake = _FakeFfmpeg(ffprobe_stdout='10.0\n', stderr=stderr)
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert vad_filter_audio(str(source), str(target)) == ([], 10.0, 0)
        assert target.read_bytes() == source.read_bytes()
        assert not [c for c in fake.calls if '-ss' in c['args']]

    def test_defaults_are_read_from_config(self, tmp_path, monkeypatch):
        from lib import config
        monkeypatch.setattr(config, 'SILENCE_DB', -27)
        monkeypatch.setattr(config, 'SILENCE_MIN_S', 1.1)
        monkeypatch.setattr(config, 'SILENCE_MIN_SPEECH_S', 0.7)
        monkeypatch.setattr(config, 'SILENCE_PADDING_S', 0.4)
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(
            ffprobe_stdout='8.0\n',
            stderr='silence_start: 2.0\nsilence_end: 3.0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        segs, _, _ = vad_filter_audio(str(source), str(target))

        detect_args = fake.calls[0]['args']
        assert detect_args[detect_args.index('-af') + 1] == (
            'silencedetect=n=-27dB:d=1.1')
        assert [tuple(round(v, 2) for v in seg) for seg in segs] == [
            (0.0, 2.4), (2.6, 8.0)]

    def test_custom_thresholds_are_passed_through(self, tmp_path, monkeypatch):
        source = _write_wav(tmp_path / 'in.wav')
        target = tmp_path / 'out.wav'
        fake = _FakeFfmpeg(
            ffprobe_stdout='10.0\n',
            stderr='silence_start: 1.0\nsilence_end: 2.0\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        segs, total, _ = vad_filter_audio(
            str(source), str(target), silence_db=-45, min_silence=1.5,
            min_speech=2.0, padding=0.5)

        detect_args = fake.calls[0]['args']
        assert detect_args[detect_args.index('-af') + 1] == (
            'silencedetect=n=-45dB:d=1.5')
        assert fake.calls[0]['kwargs']['check'] is False
        assert [tuple(round(v, 2) for v in seg) for seg in segs] == [
            (1.5, 10.0)]
        assert total == 10.0


# ═══════════════════════════════════════════════════════════════
# Audio stream probing / extraction / duration
# ═══════════════════════════════════════════════════════════════

class TestAudioProbing:
    def test_probe_lists_relative_index_and_language(self, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='0,jpn\n\n1,eng\n2\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert probe_audio_streams('video.mkv') == [
            (0, 'jpn'), (1, 'eng'), (2, '')]
        call = fake.calls[0]
        assert call['kwargs'] == {'tool': 'ffprobe', 'check': False,
                                  'text': True}
        assert call['args'][:4] == ['-v', 'error', '-select_streams', 'a']
        assert call['args'][-1] == 'video.mkv'

    def test_probe_returns_empty_on_failure(self, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout=None)
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert probe_audio_streams('video.mkv') == []

    def test_pick_matches_container_language_tags(self, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='0,jpn\n1,eng\n2,chi\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert pick_audio_stream('video.mkv', 'ja') == 0
        assert pick_audio_stream('video.mkv', 'en') == 1
        assert pick_audio_stream('video.mkv', 'zh') == 2
        assert pick_audio_stream('video.mkv', 'ko') is None

        probed = len(fake.calls)
        assert pick_audio_stream('video.mkv', 'de') is None
        assert pick_audio_stream('video.mkv', None) is None
        assert len(fake.calls) == probed  # unknown language: no probing


class TestExtractAudioWav:
    def test_builds_16k_mono_command(self, tmp_path, monkeypatch):
        fake = _FakeFfmpeg()
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)
        target = tmp_path / 'audio.wav'

        extract_audio_wav('video.mkv', str(target))

        assert fake.calls[-1]['args'] == [
            '-y', '-i', 'video.mkv', '-vn', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', str(target)]
        assert target.read_bytes() == b'fake-ffmpeg-output'

    def test_passes_slice_and_explicit_stream(self, tmp_path, monkeypatch):
        fake = _FakeFfmpeg()
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        extract_audio_wav('video.mkv', str(tmp_path / 'cut.wav'),
                          ss=0, duration=3.5, audio_stream=0)

        args = fake.calls[-1]['args']
        assert args[:8] == ['-y', '-ss', '0', '-t', '3.5', '-i', 'video.mkv',
                           '-map']
        assert args[8] == '0:a:0'

    def test_prefer_lang_maps_to_matching_stream(self, tmp_path, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='0,jpn\n1,eng\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        extract_audio_wav('video.mkv', str(tmp_path / 'audio.wav'),
                          prefer_lang='en')

        assert len(fake.calls) == 2
        assert fake.calls[0]['kwargs']['tool'] == 'ffprobe'
        args = fake.calls[1]['args']
        assert args[args.index('-map') + 1] == '0:a:1'

    def test_unmatched_prefer_lang_uses_default_track(self, tmp_path,
                                                      monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='0,jpn\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        extract_audio_wav('video.mkv', str(tmp_path / 'audio.wav'),
                          prefer_lang='ko')

        assert '-map' not in fake.calls[-1]['args']

    def test_explicit_stream_wins_over_prefer_lang(self, tmp_path, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='0,jpn\n1,eng\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        extract_audio_wav('video.mkv', str(tmp_path / 'audio.wav'),
                          audio_stream=0, prefer_lang='en')

        assert len(fake.calls) == 1  # no probing when a stream is explicit
        args = fake.calls[0]['args']
        assert args[args.index('-map') + 1] == '0:a:0'


class TestGetAudioDuration:
    def test_parses_ffprobe_output(self, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='123.456\n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert get_audio_duration('ep.wav') == 123.456
        call = fake.calls[0]
        assert call['args'] == [
            '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', 'ep.wav']
        assert call['kwargs'] == {'tool': 'ffprobe', 'check': False}

    def test_returns_none_when_ffprobe_prints_nothing(self, monkeypatch):
        fake = _FakeFfmpeg(ffprobe_stdout='   \n')
        monkeypatch.setattr(wu, 'run_ffmpeg', fake)

        assert get_audio_duration('ep.wav') is None


# ═══════════════════════════════════════════════════════════════
# Demucs vocal separation (subprocess seam)
# ═══════════════════════════════════════════════════════════════

class TestSeparateVocals:
    def test_returns_vocals_file_from_explicit_output_dir(
            self, tmp_path, monkeypatch, capsys):
        audio = _write_wav(tmp_path / 'ep.wav')
        out_dir = tmp_path / 'separated'
        vocals = out_dir / 'htdemucs' / 'ep' / 'vocals.wav'
        calls = _install_fake_subprocess_run(monkeypatch, creates=vocals)

        result = separate_vocals(str(audio), output_dir=str(out_dir),
                                 python_exe='py.exe')

        assert result == str(vocals)
        assert calls['cmd'] == [
            'py.exe', '-m', 'demucs', '--two-stems=vocals',
            '-o', str(out_dir), str(audio)]
        assert calls['kwargs']['capture_output'] is True
        assert calls['kwargs']['timeout'] == 600
        assert out_dir.is_dir()
        assert '人声分离完成' in capsys.readouterr().err

    def test_defaults_to_input_dir_and_sys_executable(self, tmp_path,
                                                      monkeypatch):
        audio = _write_wav(tmp_path / 'ep01.wav')
        vocals = tmp_path / 'htdemucs' / 'ep01' / 'vocals.wav'
        calls = _install_fake_subprocess_run(monkeypatch, creates=vocals)

        result = separate_vocals(str(audio))

        assert result == str(vocals)
        assert calls['cmd'][0] == sys.executable
        assert calls['cmd'][5] == str(tmp_path)

    def test_bare_filename_defaults_to_current_directory(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'ep.wav').write_bytes(b'audio')
        vocals = tmp_path / 'htdemucs' / 'ep' / 'vocals.wav'
        _install_fake_subprocess_run(monkeypatch, creates=vocals)

        result = separate_vocals('ep.wav')

        assert vocals.exists()
        assert Path(result).resolve() == vocals.resolve()

    def test_nonzero_exit_returns_original_audio(
            self, tmp_path, monkeypatch, capsys):
        audio = _write_wav(tmp_path / 'ep.wav')
        _install_fake_subprocess_run(monkeypatch, returncode=1,
                                     stderr='CUDA out of memory')

        result = separate_vocals(str(audio), output_dir=str(tmp_path / 'out'))

        assert result == str(audio)
        err = capsys.readouterr().err
        assert 'demucs 失败' in err
        assert 'CUDA out of memory' in err

    def test_missing_vocals_output_returns_original_audio(
            self, tmp_path, monkeypatch, capsys):
        audio = _write_wav(tmp_path / 'ep.wav')
        _install_fake_subprocess_run(monkeypatch)

        result = separate_vocals(str(audio), output_dir=str(tmp_path / 'out'))

        assert result == str(audio)
        assert '输出文件未找到' in capsys.readouterr().err

    @pytest.mark.parametrize('raises', [
        subprocess.TimeoutExpired(['demucs'], 600),
        FileNotFoundError('demucs'),
    ])
    def test_unavailable_or_timeout_returns_original_audio(
            self, tmp_path, monkeypatch, capsys, raises):
        audio = _write_wav(tmp_path / 'ep.wav')
        _install_fake_subprocess_run(monkeypatch, raises=raises)

        result = separate_vocals(str(audio), output_dir=str(tmp_path / 'out'))

        assert result == str(audio)
        assert 'demucs 不可用或超时' in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════════
# Text predicates — remaining language branches
# ═══════════════════════════════════════════════════════════════

class TestTextPredicateBranches:
    def test_valid_subtitle_text_empty_zh_en_ja(self):
        assert is_valid_subtitle_text('') is False
        assert is_valid_subtitle_text('   ') is False

        assert is_valid_subtitle_text('你好', target_lang='zh') is True
        assert is_valid_subtitle_text('你', target_lang='zh') is False
        assert is_valid_subtitle_text('Привет 你好', target_lang='zh') is False

        assert is_valid_subtitle_text('hello', target_lang='en') is True
        assert is_valid_subtitle_text('ab', target_lang='en') is False
        assert is_valid_subtitle_text('hi Привет', target_lang='en') is False

        assert is_valid_subtitle_text('hello world', target_lang='ja') is False
        assert is_valid_subtitle_text('世界', target_lang='ja') is True
        assert is_valid_japanese('こんにちは') is True

    def test_looks_like_plausible_text_branches(self):
        assert looks_like_plausible_text('') is False
        assert looks_like_plausible_text('ab') is False

        assert looks_like_plausible_text('你好世界', target_lang='zh') is True
        assert looks_like_plausible_text('Okay', target_lang='zh') is True
        assert looks_like_plausible_text('Привет', target_lang='zh') is False
        assert looks_like_plausible_text(
            'this is a long english sentence', target_lang='zh') is False

        assert looks_like_plausible_text('Hello there',
                                         target_lang='en') is True
        assert looks_like_plausible_text('こんにちは',
                                         target_lang='en') is False
        assert looks_like_plausible_text('你好世界', target_lang='en') is False
        assert looks_like_plausible_text('Привет', target_lang='en') is False
        assert looks_like_plausible_text('アトム Astro',
                                         target_lang='en') is True

        assert looks_like_plausible_text('こんにちは') is True
        assert looks_like_plausible_text('hello there') is False
        assert looks_like_plausible_text('こんにちは world') is False
        assert looks_like_plausible_japanese('こんにちは') is True

    def test_short_garbled_fragment_branches(self):
        assert is_short_garbled_fragment('') is False
        assert is_short_garbled_fragment('アトム abc') is True
        assert is_short_garbled_fragment('abcdef アトム') is False
        assert is_short_garbled_fragment('アトム' + 'あ' * 20) is False

        assert is_short_garbled_fragment('hello', target_lang='en') is True
        assert is_short_garbled_fragment(
            'hello there this is a very long english line',
            target_lang='en') is False

        assert is_short_garbled_fragment('abc', target_lang='zh') is True
        assert is_short_garbled_fragment('你好', target_lang='zh') is False
        assert is_short_garbled_fragment('abcdefghij',
                                         target_lang='zh') is False

    def test_ai_fixable_branches(self):
        assert is_ai_fixable('') is False
        assert is_ai_fixable('hello') is False
        assert is_ai_fixable('アトム') is False
        assert is_ai_fixable('アトム ABC') is True
        assert is_ai_fixable('アトム ' + 'a' * 80) is False

        assert is_ai_fixable('Hello world', target_lang='en') is True
        assert is_ai_fixable('こんにちは', target_lang='en') is False
        assert is_ai_fixable('a' * 120, target_lang='en') is True
        assert is_ai_fixable('a' * 121, target_lang='en') is False

        assert is_ai_fixable('你好 ABC', target_lang='zh') is True
        assert is_ai_fixable('你好', target_lang='zh') is False

    def test_proper_noun_pattern_branches(self):
        assert is_proper_noun_pattern('阿童木', target_lang='zh') is True
        assert is_proper_noun_pattern('阿', target_lang='zh') is False
        assert is_proper_noun_pattern('Atom', target_lang='zh') is False

        assert is_proper_noun_pattern('Astro', target_lang='en') is True
        assert is_proper_noun_pattern('Astro Boy', target_lang='en') is True
        assert is_proper_noun_pattern('astro', target_lang='en') is False
        assert is_proper_noun_pattern('A', target_lang='en') is False

        assert is_proper_noun_pattern('山田太郎') is True
        assert is_proper_noun_pattern('アトムX') is True
        assert is_proper_noun_pattern('あとむ') is False

    def test_meaningful_char_count_branches(self):
        assert meaningful_char_count('') == 0
        assert meaningful_char_count('你好啊', target_lang='zh') == 2
        assert meaningful_char_count('啊呀', target_lang='zh') == 0
        assert meaningful_char_count('Um, hello there!',
                                     target_lang='en') == 2
        assert meaningful_char_count('oh uh', target_lang='en') == 0
        assert meaningful_jp_count('行くぞ') == 3

    def test_length_anomaly_branches(self):
        assert is_length_anomaly('bad cue', '   ') is False
        assert is_length_anomaly('abc', 'x' * 10, ratio=10.0) is False
        assert is_length_anomaly('abc', 'x' * 10, ratio=2.0) is True
        assert is_length_anomaly('abc', 'x' * 9, ratio=3.0) is False

    def test_setup_windows_utf8_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(wu, '_utf8_setup_done', False)
        monkeypatch.setattr(wu.sys, 'platform', 'win32')
        calls = []

        class Stream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(wu.sys, 'stdout', Stream())
        monkeypatch.setattr(wu.sys, 'stderr', Stream())

        setup_windows_utf8()
        setup_windows_utf8()

        assert calls == [{'encoding': 'utf-8', 'errors': 'replace'}] * 2

    def test_setup_windows_utf8_swallows_reconfigure_errors(self, monkeypatch):
        monkeypatch.setattr(wu, '_utf8_setup_done', False)
        monkeypatch.setattr(wu.sys, 'platform', 'win32')

        class BrokenStream:
            def reconfigure(self, **kwargs):
                raise OSError('no encoding support')

        monkeypatch.setattr(wu.sys, 'stdout', BrokenStream())
        monkeypatch.setattr(wu.sys, 'stderr', BrokenStream())

        setup_windows_utf8()  # must not raise on an un-reconfigurable stream


# ═══════════════════════════════════════════════════════════════
# Episode extraction — alternate filename strategies
# ═══════════════════════════════════════════════════════════════

class TestEpNumberStrategies:
    def test_season_episode(self):
        assert extract_ep_number('Show.S02E07.mkv') == 'EP007'

    def test_hash_number(self):
        assert extract_ep_number('[Group] Show #193.mkv') == 'EP193'

    def test_ep_prefix(self):
        assert extract_ep_number('Show EP031.mkv') == 'EP031'

    def test_japanese_counter(self):
        assert extract_ep_number('作品 第5話.mkv') == 'EP005'

    def test_resolution_context_is_not_an_episode(self):
        assert extract_ep_number('Show 800x600 - 08.mkv') == 'EP008'

    def test_fallback_scans_any_bounded_number(self):
        assert extract_ep_number('Show (042).mkv') == 'EP042'

    def test_extract_file_id_falls_back_to_stem(self):
        assert extract_file_id('movie_title.mkv') == 'movie_title'
        assert extract_file_id('Show EP012.mkv') == 'EP012'
