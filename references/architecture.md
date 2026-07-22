# Skill 架构

> AI 调试参考：脚本结构、调用关系与数据流概览。

## 脚本总览

```
scripts/
├── run_all.py                     ← 批量编排器，逐集调 episode_workflow
├── scan/unified_scanner.py        ← 单次遍历：乱码检测 + 重复检测 + 术语收集
├── fix/
│   ├── fix_orchestrator.py        ← 统一修复模块（Fixer 类）：参考→Whisper→auto_triage
│   ├── episode_workflow.py        ← 单集编排器（大部分逻辑已迁移到 Fixer）
│   ├── whisper_pipeline.py        ← Whisper Tier 1 拼接 / Tier 2 整集 + VAD + build_clusters
│   ├── translate_srt.py           ← 百度翻译 SRT（text 模式专用）
│   ├── oped_fixer.py              ← 跨集 OP/ED 检测与修复
│   └── compare_srt.py             ← 时间码对齐 + 文本相似度比对
├── nouns/
│   ├── noun_checker.py            ← 专名一致性 + 跨集 OP/ED 统一
│   ├── auto_classify.py           ← 专名自动分类（Jamdict + 规则）
│   ├── auto_clean_glossary.py     ← 自动清理专名词表
│   └── build_glossary.py          ← 术语表自动生成
├── apply/apply_fixes.py           ← 批量修复：繁→简 + 翻译腔 + fixes + review checklist
├── ass/ass_repair.py              ← ASS 格式修补（SRT 项目跳过）
├── utils/
│   ├── update_report.py           ← 问题解决报告读写
│   └── clean_empty_cues.py        ← 清理空白 cue
└── lib/
    ├── srt_utils.py               ← SRT 解析/写回（行列表模型）
    ├── ass_utils.py               ← ASS 解析/写回（兼容 SRT）
    ├── whisper_utils.py           ← Whisper CLI + ffmpeg + VAD + 分类 + 置信度（cue 字典模型）
    ├── whisper_backends.py        ← 多后端抽象层 (whisper.cpp / faster-whisper / openai-whisper)
    ├── project_utils.py           ← 模式检测 + 文件查找 + git 备份 + 后端检测
    ├── japanese_utils.py          ← 日语常量：常见词、敬称、非对话标记
    ├── chinese_utils.py           ← 繁→简映射表 + 拼音声调
    └── _path.py                   ← PYTHONPATH 自动注入
```

## 两套 SRT 数据模型

| 模块 | 数据模型 | 用途 |
|------|---------|------|
| `srt_utils.py` | 行列表 (`list[str]`)，ASS 兼容 dict | 文件读写、逐行编辑、`apply_fixes.py` |
| `whisper_utils.py` | cue 字典列表（`start_s`, `end_s`, `text`...） | 时间码运算、乱码分类、Whisper 管线 |

两者通过 `whisper_utils.parse_srt()` 桥接。

## 脚本调用关系

```
run_all.py (唯一入口)
  ├─→ unified_scanner.py              Phase 1: 全量扫描 → findings.json
  ├─→ episode_workflow.py EPxxx       Phase 2: 逐集（subprocess）
  │     └─→ Fixer.run_auto()          (cascading: ref → Whisper → human)
  │           ├─ fix_by_reference()    → translate_srt.py + compare_srt.py
  │           ├─ fix_by_whisper()      → whisper_pipeline.py → whisper-cli.exe
  │           ├─ review_ai()           AI 短碎片清单
  │           └─ review()              人工审查清单 + 视频片段
  ├─→ step_nouns()                    Phase 3: noun_checker + auto_classify
  ├─→ step_apply_all()                Phase 3: apply_fixes（收集所有 fixes 一次应用）
  ├─→ step_ass_repair()               ASS only → SRT 项目跳过
  └─→ step_deliver()                  统一审查清单生成/应用
```

## 数据流：检测 → 修复 → 报告

```
unified_scanner (Phase 1)
  │  扫描 AI审查后/*.srt
  │  输出 findings.json → per_episode_issues[EP001] = [乱码 cue 列表]
  ▼
Fixer.run_auto() (Phase 2)
  │  读 findings.json → 知道哪些集有乱码
  │  build_clusters() → Tier 1/2 Whisper → match_whisper_to_cues()
  │  auto_triage: looks_like_plausible_japanese() → 分诊
  │  ├── 可读 → 直接写 SRT + 报告 ✅
  │  ├── 短碎片 → AI 补全 ⬜
  │  ├── 专名模式 → Phase 3 ⬜
  │  └── 长乱码 → 人工 ⬜
  ▼
noun_checker + auto_classify (Phase 3)
  │  读 proper-nouns.md 专名表 → 匹配/发现变体
  │  ACCEPT/REJECT/NEEDS_AI
  ▼
apply_fixes (Phase 3)
  │  收集所有 fixes（auto_accepted + AI review + OP/ED）
  │  一次写入所有 SRT
  └─→ 问题解决报告.md 更新各阶段状态
```

## auto_triage 分诊决策树

```
Whisper 输出 replacement
  │
  ├─ confidence='none' 或 无 replacement
  │   └─→ 人工
  │
  ├─ looks_like_plausible_japanese(replacement) → True
  │   └─→ ✅ 直接写入 SRT
  │
  └─ 不可读
      ├─ is_proper_noun_pattern(original) → Phase 3 专名审查
      ├─ is_short_garbled_fragment(replacement) → AI 上下文补全
      └─ 其余 → 人工
```
