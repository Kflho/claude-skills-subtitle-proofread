---
name: subtitle-proofread
description: >
  Automate subtitle proofreading for SRT/ASS files. 6-layer pipeline: scan garbled
  characters → fix via translation or Whisper ASR → AI confidence review → unify proper
  nouns → AI noun review → apply batch fixes (trad→simp, translationese) → repair ASS
  formatting → human review delivery. Single command: run_all.py. Supports any language
  pair (--lang ja|zh). Use when user: mentions subtitles/captions, asks to proofread/fix/
  clean SRT or ASS files, wants to run Whisper on subtitles, needs proper noun consistency,
  has machine-translated subtitles to improve, or wants to batch-process subtitle files.
  Triggers on words: 字幕, subtitle, SRT, ASS, proofread, caption, 校对, Whisper, 专有名词.
when_to_use: >
  Auto-trigger when the user's project contains SRT/ASS subtitle files OR the user
  explicitly mentions subtitles, captions, proofreading, or Whisper transcription.
  This skill provides a fully automated 6-layer pipeline — always prefer it over
  manual script-by-script execution.
argument-hint: [目标目录] [参考字幕目录]
---

# 字幕校对 Skill

> 技术实现细节 → [references/technical-details.md](references/technical-details.md)

## 架构

```
scripts/
├── run_all.py                  ← 一键全流程
├── 01_scan/unified_scanner.py   字符扫描 + 术语收集
├── 02_fix/
│   ├── fix_orchestrator.py      统一修复（参考字幕 → Whisper → 人工）
│   ├── episode_workflow.py      单集编排器
│   ├── whisper_pipeline.py      Whisper 重转录（Tier 1 拼接 / Tier 2 整集）
│   ├── translate_srt.py         百度翻译（text 模式）
│   └── compare_srt.py           时间码对齐+相似度
├── 03_nouns/
│   ├── noun_checker.py          专名一致性 + OP/ED 统一
│   └── build_glossary.py        术语表生成
├── 04_apply/apply_fixes.py      批量修复（繁→简+翻译腔+fixes）
├── 05_ass/ass_repair.py         ASS 格式修补
├── utils/
│   ├── check_progress.py        进度统计
│   ├── update_report.py         报告 6 层读写
│   ├── extract_review_clips.py  [废弃 → fix_orchestrator]
│   └── clean_empty_cues.py      清理空白 cue
└── lib/
    ├── srt_utils.py / ass_utils.py / whisper_utils.py
```

## 规则

1. **所有语言走相同工作流。** 脚本通过 `--lang ja|zh` 自适应。
2. **ASS 格式额外修补。** SRT 跳过，ASS 运行 `ass_repair.py --check all`。
3. **修复优先级（逐级降级，不可配置）。**
   1. 参考字幕翻译对照（最可靠）
   2. Whisper 人声转录（参考不可用时或参考无法匹配时）
   3. 人工审查修正（前两级都无法修复时）
4. **所有修正走同一条嵌入路径。** Whisper/翻译/人工 — 统一的 SRT 写入 + 报告更新。
5. **幂等。** 已修复的 cue → 纯目标语言 → classify_garbled_text → clean → 跳过。

## 6 层工作流

```
第1层   字符扫描     unified_scanner → VAD 删非人声 cue → 乱码/重复检测
第2层   错误修复     Fixer.run_auto() → 参考字幕 → Whisper → 人工（逐级降级）
第2.5层 AI置信度审查 筛选 Whisper 低置信度条目 → AI 保留/修正/升级
第3层   专名统一     noun_checker → 语境感知匹配 + 跨集 OP/ED 一致性
第3.5层 AI专名审查   unknown/mismatch → 抽样 top 20 → AI 判断 → fixes 或词表
第4层   批量修复     apply_fixes → 繁→简 + 翻译腔 + 所有 fixes 一次性应用
第5层   格式修补     ass_repair --check all  [ASS only]
```

## 常用命令

```bash
# 一键全流程
python scripts/run_all.py --lang ja                    # 全量
python scripts/run_all.py --lang ja --limit 5           # 前5集
python scripts/run_all.py --lang ja -e EP001-EP010      # 指定范围
python scripts/run_all.py --lang ja --resume            # AI审查后继续
python scripts/run_all.py --lang ja --apply-checklist   # 应用人工审查修正

# 单集
python scripts/02_fix/episode_workflow.py EP064         # 全流程
python scripts/02_fix/episode_workflow.py EP064 --step ai-review  # AI审查

# Fixer 直接调用
python scripts/02_fix/fix_orchestrator.py EP002 --step check     # 检查是否干净
python scripts/02_fix/fix_orchestrator.py EP002 --step whisper   # 只跑 Whisper
python scripts/02_fix/fix_orchestrator.py EP002 --step review    # 生成审查清单
python scripts/02_fix/fix_orchestrator.py EP002 --step apply --checklist <path>  # 应用修正

# 单层调试
python scripts/01_scan/unified_scanner.py --target-dir AI审查后/ --project-lang ja
python scripts/03_nouns/noun_checker.py AI审查后/ --lang ja --oped

# 备份
git add -A && git commit -m "备份：{做什么}"
```

## 项目感知

| 特征 | 行为 |
|------|------|
| SRT only | 跳过 ASS 修补 |
| ASS 格式 | +`ass_repair.py --check all` |
| 有参考字幕 | text 模式：翻译对照 |
| 无参考字幕 | audio 模式：VAD+Whisper，不猜 |
| 中文目标 | 自动繁→简 + 翻译腔去机械化 |

## 版本管理

```
项目/
├── 原始字幕/          ← 只读备份
├── AI审查后/          ← 工作目录（Git 管理）
├── temp/scans/        ← 中间结果（.gitignore）
├── temp/translations/ ← 翻译后 SRT
├── temp/compares/     ← 对照差异报告
└── reports/           ← 问题解决报告 + 审查清单 + 专名表
```

**铁律**：修改 SRT 前 `git add -A && git commit -m "备份：..."`

## 参考文档

| 文档 | 内容 |
|------|------|
| [technical-details.md](references/technical-details.md) | 各脚本实现、算法、参数 |
| [whisper-pipeline.md](references/whisper-pipeline.md) | Whisper 三层修复策略 |
| [script-templates.md](script-templates.md) | fixes.json 配置模板 |
| [full-mode.md](full-mode.md) | 完整模式（有参考字幕） |
| [setup-guide.md](setup-guide.md) | Whisper 环境搭建 |
