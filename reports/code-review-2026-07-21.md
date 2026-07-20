# Code Review Report: subtitle-proofread Skill 重构

> **范围**: `af41400...HEAD` (19 commits, 42 files, +7243/-6918)  
> **日期**: 2026-07-21  
> **双轴审查**: Standards (代码质量) + Spec (需求覆盖)

---

## Standards — 代码质量审查

### 🔴 正确性 Bug（已修复）

| # | 文件 | 行 | 问题 | 修复 |
|---|------|-----|------|------|
| 1 | `scripts/04_apply/apply_fixes.py` | 68 | `sys.path.insert(0, _root_dir)` — `_root_dir` 未定义 → **NameError** | ✅ 已添加 `_SCRIPT_DIR`/`_ROOT_DIR` |
| 2 | `scripts/05_ass/ass_repair.py` | 25 | 同上 — `_root_dir` 未定义 | ✅ 已添加 |
| 3 | `scripts/04_apply/apply_fixes.py` | 482 | `from workflow.update_report import...` → 应为 `utils.update_report` | ✅ 已修复 |
| 4 | `scripts/run_all.py` | 82,158,173,220,266,274 | 6 个脚本路径均指向旧扁平结构 (如 `unified_scanner.py` 应为 `01_scan/unified_scanner.py`) | ✅ 已修复 |
| 5 | `scripts/01_scan/unified_scanner.py` | 429 | `build_script = os.path.join(_script_dir, 'build_glossary.py')` → 应为 `os.path.join(_root_dir, '03_nouns', 'build_glossary.py')` | ✅ 已修复 |
| 6 | `scripts/03_nouns/build_glossary.py` | 12-14 | 模块 docstring 仍宣传已删除的 `--merge` 参数 | ✅ 已修复 |

### 🟡 代码异味（建议，非阻塞）

| # | 文件 | 类型 | 说明 |
|---|------|------|------|
| 7 | `compare_srt.py`, `translate_srt.py`, `noun_checker.py` (2处) | **重复代码** | SRT 解析正则完全相同的 4 个副本。`lib/srt_utils.py` 已有 `parse_srt_cue`，应统一使用 |
| 8 | `compare_srt.py:52`, `extract_review_clips.py:143` | **重复代码** | `_to_seconds()` 本地副本，`lib/whisper_utils.py:174` 已有库函数 |
| 9 | `whisper_pipeline.py:167`, `build_glossary.py:30`, `noun_checker.py:174` | **重复代码** | 3 处独立的常见片假名词表，应提取到 `lib/` 共享 |
| 10 | `apply_fixes.py:494`, `noun_checker.py:102` | **重复代码** | 繁→简映射表（~120 条目）完全重复 |
| 11 | 全局 | **Shotgun Surgery** | 目录重构后每个脚本都需要手动添加 `sys.path` 样板代码。建议用 `__init__.py` + `conftest.py` 统一管理 |

### ✅ Demucs 拼接逻辑验证

`whisper_pipeline.py` Tier 1 的「片段拼接 → demucs → 单次 Whisper → 偏移量映射」逻辑通过全部 5 项测试：

- **3 段拼接 + 2s 静音**: total=34s，偏移量精确，Whisper 段正确映射回各 cluster
- **单段**: 边界情况正确
- **空输入**: 返回空偏移量
- **demucs 时序**: htdemucs 架构保证输入输出采样数一致，时间戳保持有效
- **WAV 时长公式**: `len(frames) / (rate × nchannels × sampwidth)` 验证正确

---

## Spec — 需求覆盖审查

### ✅ 已确认实现的需求

- VAD 预扫描 (WebRTC VAD) + `--separate-vocals` ✔
- `⚠SPEECH` 占位 cue 检测无字幕语音段 ✔
- `classify_garbled_text` 简化为 2 类（clean/garbled） ✔
- 重复检测合并进 `unified_scanner` ✔
- 语境感知名词审查（ja/zh 敬称/呼唤/介绍模式） ✔
- `--build-glossary` 一步扫描+术语表 ✔
- Tier 1/2 自动升级 + retry model ✔
- `episode_workflow.py --all` 批量模式 + clean 步骤 ✔
- `ass_repair.py` 覆盖 5 种 ASS 检查 ✔
- `run_all.py` 一键全流程 ✔

### 🟡 局部缺失

| # | 需求 | 状态 |
|---|------|------|
| S1 | Layer 6 (`extract_review_clips.py`) 未接入 `run_all.py` 一键流程 | SKILL.md 列为第6层但 pipeline 只到第5层；需在 `run_all.py` 添加 `step_deliver()` |
| S2 | `references/detection-scripts.md` 仍引用已删除的脚本 (`romaji_fixer.py`, `garbled_detect.py`, 等 6 个) | 文档需更新 |
| S3 | `references/whisper-pipeline.md` 第 34 行引用已删除的 `romaji_fixer.py` | 文档需更新 |
| S4 | 删除数统计偏差：commit `e1067d4` 称"14 个废弃脚本"，实际删除 19 个文件 | 提交信息不精确（额外删除的 5 个包括 moved/merged 文件） |

---

## 总结

| 维度 | 关键 Bug 数 | 已修复 | 遗留建议 |
|------|------------|--------|---------|
| **Standards** | 6 | 6 ✅ | 5 个代码异味（重复代码/Shotgun Surgery） |
| **Spec** | 0 关键缺失 | — | 1 个功能缺失（Layer 6）+ 3 个文档过期 |

**最严重问题（已修复）**: `apply_fixes.py` 和 `ass_repair.py` 的 `_root_dir` 未定义导致 **NameError**，`run_all.py` 的 6 个脚本路径全部指向旧目录结构导致 **FileNotFoundError**——任何一键运行都会崩溃。

**最大剩余风险**: `extract_review_clips.py`（Layer 6 人工交付）已实现但未接入 `run_all.py`，使用者需手动运行。
