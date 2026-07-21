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
L3 专名统一  → 四步流水线:
                L3.0 build_glossary → 激进出词表 (JMdict就删)
                L3.1 auto_clean_glossary → 脚本启发式粗筛
                L3.2 AI词库审查 → 语义终审 (脚本漏网之鱼)
                L3.3 noun_checker + auto_classify → 匹配SRT
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

# 专名表维护（L3.0-L3.2）
python nouns/build_glossary.py --findings temp/scans/findings.json -o reports/proper-nouns.md
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md          # L3.1 dry-run
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md --apply  # L3.1 自动清理
# L3.2 AI词库审查: 调用 subtitle-proofread skill → Claude 审查剩余条目
```

### L3.2 AI 词库审查

脚本粗筛后仍有少量漏网之鱼。高频条目（水博士 270、科学省 96）
明显是真专名，不需要审。AI 只审查**低频 + 可疑**条目。

**触发**: 调用 subtitle-proofread skill → 「审查专名表」
**输入**: `proper-nouns.md` 中 freq ≤ 8、不含姓氏/地名特征的条目（~30 条）
**输出**: 追加到 COMMON_KANJI 的词
**成本**: ~30 条目 × 简单分类 ≈ 1,500 tokens，一次性

**为什么脚本 + AI 各司其职：**

| | L3.1 脚本 | L3.2 AI |
|------|------|------|
| 处理量 | 17,548 → ~600 | ~30（低频可疑） |
| 擅长 | 字典/模式匹配 | 语义理解 |
| 漏网 | 競技大会、慶応生… | — |
| Token | 0 | ~1,500 |

> **这是一个维护步骤，不参与每次 run_all.py 执行。**
> 建表 → AI 审一次 → commit → 后续直接复用。

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
