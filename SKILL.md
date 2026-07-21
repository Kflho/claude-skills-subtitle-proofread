---
name: subtitle-proofread
description: >
  6-layer subtitle proofreading pipeline for SRT/ASS. Scan garbled chars → Whisper ASR
  fix → AI fragment completion → proper noun unification → batch fixes → human review.
  Single command: run_all.py. Use when user mentions: 字幕, subtitle, SRT, ASS,
  proofread, 校对, Whisper, 专有名词, captions, or project contains SRT/ASS files.
when_to_use: >
  Auto-trigger when project contains SRT/ASS subtitle files or user mentions
  subtitles, captions, proofreading, or Whisper transcription.
argument-hint: [目标目录] [参考字幕目录]
---

# 字幕校对 Skill

> 技术实现 → [references/technical-details.md](references/technical-details.md)
> 命令速查 → [references/detection-scripts.md](references/detection-scripts.md)

## 6 层工作流与规则

```
L1 字符扫描  → unified_scanner: 乱码/重复检测
L2 错误修复  → Fixer: 参考字幕 → Whisper → auto_triage
                ├─ 可读 → 写入SRT
                ├─ 短碎片 → L2.5 AI补全
                ├─ 专名模式 → L3
                └─ 长乱码 → L6人工
L2.5 AI补全 → VAD有人声+不可读+短碎片 → AI上下文推测
L3 专名统一  → build_glossary → auto_clean_glossary → noun_checker + auto_classify
                ├─ 激进策略: 在 JMdict 就删（不管 JMnedict）
                ├─ 启发式: 动词词干/时间碎片/修饰语片段 → 自动过滤
                └─ 只保留人名 + 动画特有概念
L3.5 AI审查 → auto_classify拿不准的 → AI判断
L4 批量修复  → apply_fixes: 繁→简 + 翻译腔 + fixes
L5 格式修补  → ASS only, 本项目跳过
```

1. **修复优先级逐级降级**：参考字幕 → Whisper → AI短碎片补全 → 人工审查
2. **所有修正走同一条嵌入路径**：统一 SRT 写入 + 报告更新
3. **幂等**：已修复 cue → `classify_garbled_text` → clean → 跳过
4. **SRT 是唯一真相源**：修改前 `git add -A && git commit -m "备份：..."` 
5. **ASS 额外修补**：SRT 跳过，ASS 运行 `ass_repair.py --check all`

## 人工审查铁律

1. **Whisper ≠ 人工审查 — 片段长度不同。** Whisper 用完整 cluster（声学上下文）；人工审查只取乱码段 + 每侧 ≤5s padding。
2. **必须保留视频片段。** 人需要画面判断（口型、场景）。`review()` 提取视频 clip，不可降级为纯音频。
3. **VAD 时间对齐 + 稳健 fallback。** 1段→替换边界；N≠1段→保持原边界；0段→标记无人声。
4. **统一审查清单。** `reports/manual-review/checklist.md` 一份文件覆盖所有集。
5. **乱码 cue 零遗漏。** Whisper 无输出 / 无 cluster / VAD 删除 → 必须路由到 L6 人工。

## 常用命令

```bash
python scripts/run_all.py --lang ja                    # 一键全流程
python scripts/run_all.py --lang ja --limit 5           # 前5集
python scripts/run_all.py --lang ja -e EP001-EP010      # 指定范围
python scripts/run_all.py --lang ja --apply-checklist   # 应用人工审查

# 专名表维护
python nouns/build_glossary.py --findings temp/scans/findings.json -o reports/proper-nouns.md
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md          # dry-run
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md --apply  # 自动清理
```

## 项目感知

| 特征 | 行为 |
|------|------|
| SRT only | 跳过 ASS 修补 |
| ASS 格式 | +`ass_repair.py --check all` |
| 有参考字幕 | text 模式：翻译对照 |
| 无参考字幕 | audio 模式：VAD+Whisper，不猜 |
| 中文目标 | 自动繁→简 + 翻译腔去机械化 |

## 参考文档

| 文档 | 内容 |
|------|------|
| [technical-details.md](references/technical-details.md) | 架构、数据流、算法、参数 |
| [whisper-pipeline.md](references/whisper-pipeline.md) | Whisper 三层修复策略 |
| [detection-scripts.md](references/detection-scripts.md) | 所有脚本命令速查 |
| [full-mode.md](references/full-mode.md) | 完整模式（有参考字幕） |
| [setup-guide.md](setup-guide.md) | Whisper 环境搭建 |
