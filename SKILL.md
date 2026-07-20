---
name: subtitle-proofread
description: >
  Automate subtitle proofreading for SRT/ASS files. 5-layer pipeline: scan garbled
  characters → fix via translation or Whisper ASR → unify proper nouns → apply batch
  fixes (trad→simp, translationese) → repair ASS formatting. Single command: run_all.py.
  Supports any language pair (--lang ja|zh). Use when user: mentions subtitles/captions,
  asks to proofread/fix/clean SRT or ASS files, wants to run Whisper on subtitles,
  needs proper noun consistency, has machine-translated subtitles to improve, or
  wants to batch-process subtitle files. Triggers on words: 字幕, subtitle, SRT, ASS,
  proofread, caption, 校对, Whisper, 专有名词.
when_to_use: >
  Auto-trigger when the user's project contains SRT/ASS subtitle files OR the user
  explicitly mentions subtitles, captions, proofreading, or Whisper transcription.
  This skill provides a fully automated 5-layer pipeline — always prefer it over
  manual script-by-script execution.
argument-hint: [目标目录] [参考字幕目录]
---

# 字幕校对 Skill

## 架构

```
scripts/
├── run_all.py                  ← 一键全流程
├── 01_scan/
│   └── unified_scanner.py      字符扫描 + 术语收集
├── 02_fix/
│   ├── episode_workflow.py     编排器
│   ├── translate_srt.py        百度翻译（text模式，优先）
│   ├── compare_srt.py          时间码对齐+相似度
│   └── whisper_pipeline.py     Whisper重转录（audio模式，回退）
├── 03_nouns/
│   ├── noun_checker.py          专名一致性 + OP/ED统一 + AI审查标记
│   └── build_glossary.py        术语表生成
├── 04_apply/
│   └── apply_fixes.py           批量修复（繁→简+翻译腔+fixes应用）
├── 05_ass/
│   └── ass_repair.py            ASS格式修补
├── utils/
│   ├── check_progress.py        进度统计
│   ├── update_report.py         报告查询（问题解决报告.md）
│   ├── extract_review_clips.py   人工审查交付（视频片段+清单）
│   └── clean_empty_cues.py      清理空白cue
└── lib/
    ├── ass_utils.py             ASS/SRT解析
    ├── srt_utils.py             SRT解析
    └── whisper_utils.py         Whisper共享工具
```

## 两条规则

**规则 1：所有语言走相同工作流。** 脚本通过 `--lang ja|zh` 自适应。

**规则 2：ASS 格式额外修补。** SRT 跳过，ASS 运行 `ass_repair.py --check all`。

## 6 层工作流（+ AI 审查层 + 人工审查交付）

```
统一工作流（--lang ja|zh）：

第1层 字符扫描    workflow/unified_scanner
                    VAD 语音检测 → 自动删除非人声 cue ([音楽][拍手]等)
                    可选: --detect-missing-dialogue → 检测漏句加占位 cue
第2层 语义修复    workflow/episode_workflow
                    ├─ [text]  translate_srt → compare_srt
                    └─ [audio] whisper_pipeline → VAD clean → Whisper Tier 1/2
第3层 专名统一    workflow/noun_checker
                    --noun-table 名词表对照
                    --oped 跨集OP/ED一致性
      [AI审查]    见下方「第3.5层」
第4层 批量修复    workflow/apply_fixes
第5层 格式修补    workflow/ass_repair --check all   [ASS only]
第6层 人工交付    workflow/extract_review_clips    ← 不可自动修复的交给人类
                    收集 Whisper unfixable + 专名 unresolved + report step16
                    → ffmpeg 提取视频片段 → review-checklist.md
```

### 第 3.5 层：AI 专名抽样审查

noun_checker 跑完后，如果 unknown/mismatch > 0，触发 AI 抽样审查：

**触发条件**：noun_checker 输出中 `stats.unknown > 0` 或 `stats.mismatch > 0`

**审查流程**：
1. 从 noun_checker 输出的 JSON 中提取 `status=unknown` 和 `status=mismatch` 的条目
2. 去重（同一 candidate 只保留一条），按出现频率排序
3. 抽样 top 20 条（频率最高的未知专名/变体）
4. 对每条判断：
   - 是不是真正的专有名词？（否则是普通词汇，加入排除表）
   - 规范写法是什么？（对照已知词表、跨集上下文、Web 搜索）
   - 是否有跨集拼写变体需要统一？
5. 输出 fixes.json → 走 apply_fixes 应用
6. 新发现的专名 → 补充到 reports/proper-nouns.md

**审查 Prompt 模板**：
```
你是字幕专名审查员。请审查以下 noun_checker 发现的疑似专名问题：

已知专名表: reports/proper-nouns.md（如有）
目标语言: {ja|zh}

对每个条目判断：
1. 是否为真正的专有名词（人名/地名/组织/术语）？
   普通词汇 → 标记 skip，建议加入排除表
   专有名词 → 给出规范写法
2. 当前写法是否正确？不对 → 给出修正
3. 是否有跨集拼写变体？有 → 统一为最高频形式

输出格式（每行一条）：
状态 | candidate | 规范形式 | 原因
```

**开销**：每次审查 ~20 条 × ~100 token ≈ 2000 token，几乎免费。

### 第 6 层：人工审查交付（extract_review_clips.py）

自动化无法修复的条目 → 提取视频片段 → 生成审查清单交给人类。

**触发时机**：Whisper 修复完成后，仍有 `confidence=none` 的条目；或 noun_checker 有 unresolved 项。

**用法**：
```bash
# 从 whisper fixes 收集
python scripts/utils/extract_review_clips.py \
    --fixes temp/scans/*_fixes.json \
    --video-dir "E:/Animation/TV/..." \
    --srt-dir AI审查后/ \
    --output reports/manual-review/

# 从报告 step 16 收集 (⬜ pending)
python scripts/utils/extract_review_clips.py \
    --report reports/问题解决报告.md --step 16 \
    --video-dir "..." --srt-dir AI审查后/ \
    --output reports/manual-review/

# 从专名检查收集
python scripts/utils/extract_review_clips.py \
    --noun-check temp/scans/noun_check.json \
    --video-dir "..." --output reports/manual-review/

# 预览（不提取视频）
python scripts/utils/extract_review_clips.py \
    --fixes temp/scans/*_fixes.json \
    --video-dir "..." --output reports/manual-review/ \
    --dry-run
```

**输出**：
- `reports/manual-review/EPxxx_HH-MM-SS-sss.mp4` — 每个待审查条目一个短视频片段（前后 3s padding）
- `reports/manual-review/review-checklist.md` — 审查清单模板，人类在「修正:」后填写正确台词

**清单格式**：
```markdown
EP064 | 00:24:28.250 ~ 00:24:32.000 | EP064_00-24-28-250.mp4
来源: Whisper unfixable
残留: anaねえ降れ
修正:

---
```

人类填写「修正:」后，用 Whisper VAD 自动打轴应用到 SRT。

## 常用命令

```bash
# 一键全流程（推荐）
python scripts/run_all.py --lang ja                    # 全量
python scripts/run_all.py --lang ja --limit 5           # 前5集
python scripts/run_all.py --lang ja -e EP001-EP010      # 指定范围
python scripts/run_all.py --lang ja --start-from EP050  # 从EP050开始
python scripts/run_all.py --lang ja --resume            # AI审查后继续
python scripts/run_all.py --lang ja --apply-ai-review   # 仅应用AI审查修复

# 单层调试
python scripts/01_scan/unified_scanner.py --target-dir AI审查后/ --project-lang ja
python scripts/03_nouns/noun_checker.py AI审查后/ --lang ja --oped
python scripts/utils/check_progress.py

# 备份
git add -A && git commit -m "备份：{做什么}"
```

## 项目感知模式匹配

| 项目特征 | 自动行为 |
|----------|---------|
| 只有目标字幕 | 分支 A（必需步骤） |
| +参考字幕 | +分支 C（翻译对照），加载 `full-mode.md` |
| +视频 + whisper | +分支 B（Whisper管线），加载 `whisper-pipeline.md` |
| SRT only | 跳过分支 D（ASS修补） |
| ASS 格式 | +分支 D：`ass_repair.py --check all` |
| 无参考字幕 | audio 模式：VAD+Whisper，不猜 |
| 无 whisper | 加载 `setup-guide.md` |

## 版本管理

```
项目/
├── 原始字幕/          ← 只读备份（.gitignore）
├── 参考字幕/          ← （可选）准确参考字幕
├── AI审查后/          ← 工作目录（Git管理）
├── temp/              ← 临时文件（.gitignore）
│   ├── audio/         ← VAD音频片段
│   ├── whisper/       ← Whisper原始输出
│   ├── scans/         ← 扫描中间结果
│   ├── translations/  ← 翻译后SRT
│   └── compares/      ← 对照差异报告
└── reports/           ← 问题解决报告 + 审查清单
```

**铁律**：修改 SRT 前 `git add -A && git commit -m "备份：..."`

## 技术备忘

- 所有脚本通过 `--lang ja|zh` 自适应，无需工作流分支
- `apply_fixes --lang zh` 自动执行：繁→简（150+映射）+ 翻译腔去机械化（15条EN→ZH正则）
- `noun_checker --oped` 跨集 OP/ED 一致性：按时码分桶→发现变体→最高频为规范
- ASS: `split(',', 9)` 防逗号破坏；跳过 Display 样式
- SRT: `utf-8-sig` BOM；无 Name/Style 字段
- Whisper: `-nth 0.6 -mc 0`；WAV 无损管道；不加 `-sns`
- 百度翻译: MD5 签名，1 QPS，200万字符/月免费
