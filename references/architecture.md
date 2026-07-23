# Skill 架构

> AI 调试参考：脚本结构、调用关系与数据流概览。

## 脚本总览

```
scripts/
├── run_all.py                     ← 批量编排器，逐集调 episode_workflow
├── scan/unified_scanner.py        ← 单次遍历：乱码检测 + 重复检测 + 术语收集 + (v5.0) VAD 无字幕检测
├── fix/
│   ├── fix_orchestrator.py        ← 统一修复模块（Fixer 类）：参考→Whisper→auto_triage
│   ├── episode_workflow.py        ← 单集编排器（大部分逻辑已迁移到 Fixer）
│   ├── whisper_pipeline.py        ← Whisper Tier 1 拼接 / Tier 2 整集 + VAD + build_clusters
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
  │     └─ (v5.0) VAD 扫描: 提取音频 → WebRTC VAD → missing_subtitles
  │           缓存 speech timeline → temp/scans/EPxxx_vad.json
  ├─→ episode_workflow.py EPxxx       Phase 2: 逐集（subprocess）
  │     └─→ Fixer.run_auto()          (cascading: ref → Whisper → missing-sub fill)
  │           ├─ fix_by_reference()    → translate_srt.py + compare_srt.py
  │           ├─ fix_by_whisper()      → whisper_pipeline.py → whisper-cli.exe
  │           │     └─ 复用 Phase 1 VAD 缓存 (避免重复提取音频)
  │           ├─ fix_missing_subtitles() (v5.0) → gap 音频 → Whisper → 插入新 cue
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

## v5.0: VAD 有人声无字幕检测

### 设计理念

"有人声无字幕" 是字幕文件的 **第一类错误**，与乱码字符并列。v5.0 将其从 Phase 2 的副作用升级为 Phase 1 的正式检测。

### 数据流

```
Phase 1 (Scan)
  unified_scanner.py --video-dir <DIR>
    │
    ├─ 文本扫描 (existing): garbled_cues → findings.json
    │
    └─ VAD 扫描 (NEW):
        1. _find_video_for_srt() → 匹配视频文件（集号匹配）
        2. extract_audio_wav() → 16kHz mono WAV
        3. whisper_pipeline.get_speech_timeline() → WebRTC VAD
        4. whisper_pipeline.find_missing_subtitle_gaps() → 发现 gaps
        5. 写入 findings.json:
           {
             "missing_subtitles": {
               "EP001": [{"start_s": 120.5, "end_s": 125.3, "duration": 4.8}, ...]
             }
           }
        6. _save_vad_cache() → temp/scans/EPxxx_vad.json (Phase 2 复用)

Phase 2 (Fix)
  Fixer.fix_missing_subtitles()
    │
    1. 读 findings.json → missing_subtitles[episode]
    2. 每个 gap: extract_audio_wav(ss, dur) → run_whisper()
    3. 成功: is_valid_subtitle_text() → 插入新 cue (format_tc)
    4. 失败: → 插入 [???] 标记 cue
    5. write_srt() (插入 + 排序 + 重编号)
    6. upsert_entries() → 问题解决报告.md (Layer 2)

  Fixer.fix_by_whisper() — VAD clean 复用:
    │
    1. _load_speech_segs() → 尝试读取 Phase 1 缓存
    2. 缓存命中: _apply_vad_clean_from_cache() (跳过音频提取)
    3. 缓存未命中: 原有流程 (extract + VAD)
```

### 关键常量

| 常量 | 值 | 位置 |
|------|-----|------|
| `MISSING_SUBTITLE_MIN_GAP` | 3.0s | unified_scanner.py |
| `MISSING_SUBTITLE_MERGE_GAP` | 5.0s | unified_scanner.py |
| `MISSING_SUBTITLE_MAX_GAP` | 45.0s | unified_scanner.py (跳过 OP/ED 歌曲) |
| `SUSPICIOUS_GAP_SEC` | 20.0s | unified_scanner.py (文本启发式阈值) |

### 新增 CLI 标志

| Flag | 用途 |
|------|------|
| `--video-dir <DIR>` | 启用 Phase 1 VAD 检测 + Phase 2 Whisper |
| `--skip-vad` | 跳过 VAD 检测（即使有 --video-dir） |
| `--vad-cache-dir <DIR>` | VAD 缓存目录（默认 temp/scans/） |

### 向后兼容

- 无 `--video-dir` → Phase 1 仅字符扫描，Phase 2 跳过（残血模式）
- Phase 2 仍保留 fallback: 如 Phase 1 未产生 findings，`fix_by_whisper()` 的 `detect_missing_dialogue` 参数仍可在 Phase 2 做内联检测
