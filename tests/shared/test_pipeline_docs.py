"""文档结构：主文档管全流程，字幕细节降到子文档。

这条测试锁的是**结构**，不是文字：需求笔记（sk 01 note）把 skill 定位成
「前期调研 → 资源下载 → 转录字幕 → 按已有资源优化」的整条生产线，所以主文档
必须能一路路由下去，字幕操作手册只能当子文档。改文档时别把细节又堆回主文档。
"""

import io
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'SKILL.md'
SUBTITLE = ROOT / 'references' / 'subtitle.md'
RESEARCH = ROOT / 'references' / 'research.md'

STAGES = {
    '0 调研': ('调研',),
    '1 获取': ('获取',),
    '2 转录': ('转录',),
    '3 校对': ('校对',),
    '4 翻译': ('翻译', '交付'),
}


def _text(path: Path) -> str:
    return io.open(path, encoding='utf-8').read()


def test_main_doc_routes_the_whole_pipeline():
    text = _text(SKILL)
    assert '全流程' in text
    for stage, words in STAGES.items():
        assert any(word in text for word in words), f'主文档缺少阶段：{stage}'


def test_main_doc_tables_link_every_stage_to_a_subdoc():
    text = _text(SKILL)
    for target in ('references/research.md', 'references/acquire.md',
                   'references/subtitle.md', 'references/translation.md'):
        assert target in text, f'主文档没有路由到 {target}'


def test_subtitle_manual_moved_out_of_the_main_doc():
    """字幕细节（专名审查/ASS/Flags）只在子文档里展开。"""
    main = _text(SKILL)
    sub = _text(SUBTITLE)
    for heading in ('## 专名审查', '## ASS 格式项目', '## Flags'):
        assert heading in sub, f'子文档缺少：{heading}'
        assert heading not in main, f'字幕细节又回到主文档了：{heading}'


def test_main_doc_states_the_xunlei_rule():
    """迅雷确认走 kf-service 任务脚本；远程协作期间不开迅雷。"""
    text = _text(SKILL)
    assert 'kf-personal-service' in text
    assert 'thunder-download' in text
    assert '--allow-thunder' in text
    assert '远程协作期间不开迅雷' in text


def test_main_doc_is_a_router_not_a_manual():
    """主文档只放常驻层：超过 150 行说明细节又堆回来了（kf-personal-development 的写 skill 规范）。"""
    lines = _text(SKILL).splitlines()
    assert len(lines) <= 150, f'SKILL.md 有 {len(lines)} 行，细节应下沉 references/'


def test_main_doc_declares_layers_and_boundaries():
    text = _text(SKILL)
    assert '## 何时加载与能力分层' in text
    assert '## 能力边界' in text
    assert '## 阶段路由' in text


def test_moved_sections_live_in_their_subdocs():
    """排错、开发规矩、输出目录都下沉了，别又搬回主文档。"""
    main = _text(SKILL)
    for heading in ('## 错误恢复', '## 子文档索引', '## 输出目录约定'):
        assert heading not in main, f'已经下沉的小节又回到主文档：{heading}'
    assert '## 1 跑不起来' in _text(ROOT / 'references' / 'troubleshooting.md')
    assert '## 3 能力清单' in _text(ROOT / 'references' / 'development.md')
    assert '## 6 输出目录' in _text(ROOT / 'references' / 'config.md')



def test_research_doc_lists_the_subtitle_sites():
    """段 0 的字幕调研要覆盖笔记列的四类站。"""
    text = _text(RESEARCH)
    for site in ('jimaku', 'kitsunekko', 'assrt', 'opensubtitles'):
        assert site in text.lower(), f'调研文档缺少站点：{site}'


def test_research_doc_records_the_story_and_glossary_steps():
    text = _text(RESEARCH)
    assert 'external-references.md' in text, '故事背景要接外部参考档案'
    assert 'glossaries' in text, '专名表要落到 data/glossaries'


def test_docs_dont_promise_unverified_indexer():
    """没实测过的索引站不许写成已支持（笔记列了 ta.sefan.cc，但端点未验证）。"""
    acquire = _text(ROOT / 'references' / 'acquire.md')
    if 'ta.sefan.cc' in acquire:
        assert re.search(r'ta\.sefan\.cc[^\n]*未验证|未接入', acquire), \
            'ta.sefan.cc 若出现在文档里，必须标明未验证/未接入'
