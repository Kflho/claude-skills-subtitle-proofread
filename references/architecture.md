# Skill 架构

> AI 调试参考：脚本结构、调用关系与数据流概览。

## 脚本总览

```
scripts/
├── run_all.py                     ← 批量编排器，逐集调 episode_workflow
├── scan/unified_scanner.py        ← 单次遍历：乱码检测 + 重复检测 + 术语收集 + VAD 语音时间线提取
├── fix/
│   ├── fix_orchestrator.py        ← 统一修复模块（Fixer 类）：参考→Whisper→auto_triage
│   ├── episode_workflow.py        ← 单集编排器（大部分逻辑已迁移到 Fixer）
│   ├── whisper_pipeline.py        ← Whisper Tier 1/2 + VAD + build_fix_regions (unified region builder)
│   ├── translate_srt.py           ← 百度翻译 SRT（text 模式专用）
│   ├── oped_fixer.py              ← 跨集 OP/ED 检测与修复
│   └── compare_srt.py             ← 时间码对齐 + 文本相似度比对
├── nouns/
│   ├── noun_checker.py            ← 专名一致性 + 跨集 OP/ED 统一
│   ├── auto_classify.py           ← 专名分类（独立工具，pipeline 不调用）
│   ├── auto_clean_glossary.py     ← 专名词表清理（独立工具，pipeline 不调用）
│   └── build_glossary.py          ← 术语表自动生成
├── apply/apply_fixes.py           ← 批量修复：繁→简 + 翻译腔 + fixes
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
  │     ├─ 字符扫描: garbled chars + repeats + term freq
  │     └─ VAD 语音时间线: 提取音频 → WebRTC VAD → 缓存 speech timeline
  │           → temp/scans/EPxxx_vad.json
  ├─→ episode_workflow.py EPxxx       Phase 2: 逐集（subprocess）
  │     └─→ Fixer.run_auto()          (unified VAD-driven single path)
  │           ├─ fix_by_reference()    → translate_srt.py + compare_srt.py
  │           ├─ fix_by_whisper()      → whisper_pipeline.py → whisper-cli.exe
  │           │     ├─ 复用 Phase 1 VAD 缓存
  │           │     └─ build_fix_regions() 统一检测（garbled/partial/missing）
  │           └─ review_ai()           AI 短碎片清单 → [???] 标记写入 SRT
  ├─→ step_nouns()                    Phase 3: noun_checker → AI review
  ├─→ step_apply_all()                Phase 3: apply_fixes（收集所有 fixes 一次应用）
  ├─→ step_ass_repair()               ASS only → SRT 项目跳过
  └─→ step_deliver()                  残血模式报告 + [???] 标记统计
```

## 数据流：检测 → 修复 → 报告

```
unified_scanner (Phase 1)
  │  扫描 AI审查后/*.srt
  │  输出 findings.json → per_episode_issues[EP001] = [乱码 cue 列表]
  │  VAD 缓存 → temp/scans/EP001_vad.json
  ▼
Fixer.run_auto() (Phase 2)
  │  读 findings.json → 知道哪些集有乱码
  │  复用 Phase 1 VAD 缓存 → build_fix_regions()
  │  转为 cluster 格式 → Tier 1/2 Whisper → match_whisper_to_cues()
  │  auto_triage: looks_like_plausible_japanese() → 分诊
  │  ├── 可读 → 直接写 SRT + 报告 ✅
  │  ├── 短碎片 → AI 补全 ⬜
  │  ├── 专名模式 → Phase 3 ⬜
  │  └── 长乱码 → 人工 ⬜
  ▼
noun_checker → AI review (Phase 3)
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

## VAD 统一修复架构

### 设计理念

修复管线是**统一 VAD 驱动**的单路径（原为乱码修复 + 缺字幕补全双路径，已合并）。
核心思想：VAD 人声段落是对话的原子单位 — 所有字幕修复都围绕 VAD speech segment 展开。

`build_fix_regions()` 一个函数替代了 `build_clusters()` + `find_missing_subtitle_gaps()` + `add_placeholder_cues()`，
统一检测三种场景：乱码、部分重叠、缺字幕。删除 ⚠SPEECH 占位符机制。

### 数据流

```
Phase 1 (Scan)
  unified_scanner.py --video-dir <DIR>
    │
    ├─ 文本扫描: garbled_cues → findings.json
    │
    └─ VAD 扫描:
        1. _find_video_for_srt() → 匹配视频文件
        2. extract_audio_wav() → 16kHz mono WAV
        3. get_speech_timeline() → WebRTC VAD → speech_segs
        4. _save_vad_cache() → temp/scans/EPxxx_vad.json
        (不再做 gap detection — 移至 Phase 2)

Phase 2 (Fix, unified)
  Fixer.fix_by_whisper()
    │
    1. _load_speech_segs() → 读取 Phase 1 VAD 缓存
    2. vad_delete_nonspeech() → 删除纯非人声 cue
    3. build_fix_regions(speech_segs, cues) → 统一检测:
       ├─ type=garbled:        人声覆盖乱码 cue → fix region
       ├─ type=partial_overlap: 人声部分覆盖 cue+延伸到 uncovered → fix region
       └─ type=missing:        人声完全无 cue → fix region
    4. _regions_to_clusters() → 转为 cluster 格式
    5. Tier 1 (concat) 或 Tier 2 (full-ep) Whisper
    6. Triage → auto-keep / AI fragments / auto-cut
    7. cues_to_clear → orchestrator 删除被覆盖的 cue
    8. new_cues → orchestrator 插入新 cue
```

### 关键新增函数

| 函数 | 位置 | 用途 |
|------|------|------|
| `build_fix_regions()` | whisper_pipeline.py | 以 VAD speech segment 为 ground truth 统一构建 fix region |
| `_regions_to_clusters()` | whisper_fixer.py | 将 fix regions 转为 Tier 1/2 兼容的 cluster 格式 |
| `_collect_cues_to_clear()` | whisper_fixer.py | 从 regions 收集所有待删除的 overlapped cues |
| `extract_speech_timeline()` | unified_scanner.py | Phase 1 纯语音时间线提取（不做 gap 检测） |

### 保留的核心函数

| 函数 | 用途 |
|------|------|
| `get_speech_timeline()` | WebRTC VAD 引擎 |
| `vad_delete_nonspeech()` | VAD 非人声 cue 删除 |
| `run_tier1()` / `run_tier2()` | Whisper 执行层（接受 cluster 格式，未变） |
| `match_whisper_to_cues()` | Whisper 输出回贴 cue |
| `build_clusters()` | 保留为向后兼容（标记 deprecated） |

### 向后兼容

- 无 `--video-dir` → Phase 1 仅字符扫描，Phase 2 跳过（残血模式）
- Phase 2 不依赖 Phase 1 findings — VAD speech timeline 从缓存读取
- 缓存未命中时 Phase 2 实时运行 VAD（`extract_audio_wav` + `get_speech_timeline`）
