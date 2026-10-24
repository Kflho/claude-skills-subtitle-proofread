"""The skill frontmatter is the only thing the router sees — lock its quality."""

import io
import re

SKILL = 'SKILL.md'

TRIGGERS = ('字幕', '校对', '翻译', '专名', '术语', '时间轴', 'OP', '固定台词',
            'SRT', 'ASS', '开发')


def _read():
    raw = io.open(SKILL, encoding='utf-8').read()
    return raw


def _frontmatter(raw):
    assert raw.startswith('---\n'), 'SKILL.md must start with YAML frontmatter'
    end = raw.find('\n---\n', 4)
    assert end > 0, 'unterminated frontmatter'
    block = raw[4:end]
    data = {}
    key = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if re.match(r'^[a-z_]+:', line):
            key, _, value = line.partition(':')
            data[key.strip()] = value.strip()
        elif key:
            data[key] += ' ' + line.strip()
    return data, raw[end + 5:]


def test_frontmatter_parses_and_names_the_skill():
    data, _ = _frontmatter(_read())
    assert data['name'] == 'subtitle-proofread'


def test_description_is_short_enough_for_a_router():
    data, _ = _frontmatter(_read())
    description = ' '.join(data['description'].split('>').pop().split())
    assert len(description) <= 320, f'description too long: {len(description)}'


def test_description_carries_the_words_users_actually_type():
    data, _ = _frontmatter(_read())
    description = data['description']
    missing = [word for word in TRIGGERS if word not in description]
    assert not missing, f'description lacks trigger words: {missing}'


def test_description_is_not_a_changelog():
    data, _ = _frontmatter(_read())
    description = data['description']
    for noise in ('diagnostic-only', 'FireRedVAD', 'Qwen3-ASR', 'ForcedAligner'):
        assert noise not in description, f'status/implementation noise: {noise}'


def test_body_explains_when_to_load_it():
    _, body = _frontmatter(_read())
    assert '## 何时加载' in body
    assert '开发' in body.split('## 何时加载', 1)[1][:600]


def test_file_is_utf8_without_bom():
    raw = open(SKILL, 'rb').read()
    assert not raw.startswith(b'\xef\xbb\xbf')
    raw.decode('utf-8')
