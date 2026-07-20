---
name: subtitle-proofread
description: 对照参考字幕，校对机翻 ASS/SRT 字幕。支持任意语言机器翻译字幕的批量校对，含 AI 语音识别字幕。
argument-hint: [目标目录] [参考字幕目录]
---

# 字幕校对 Skill

## 版本管理

```
项目/
├── 原始字幕/          ← 只读备份（.gitignore），只能手动 cp 恢复
├── AI审查后/     ← 工作目录（Git 管理）
└── reports/           ← 问题解决报告 + 审查清单
```

**铁律**：修改 SRT 前 `git add -A && git commit -m "备份：..."`；回滚 `cp 原始字幕/XXX.srt AI审查后/`

## 启动：项目感知模式匹配

| 项目特征 | 自动行为 |
|----------|---------|
| 只有目标字幕 | 分支 A（必需步骤） |
| +参考字幕 | +分支 C（对照验证翻译），加载 `references/full-mode.md` |
| +视频 + whisper | +分支 B（音频重转录），加载 `references/whisper-pipeline.md` |
| SRT only（无 .ass 文件） | 跳过 ASS 专用脚本（names/styles/drawing/comment/oped） |
| 日语源语言 | 跳过 trad_to_simp、garbled_detect（中文幻觉）、interjection_detect |
| 无参考字幕 | 跳过 proper_noun_detect、full-mode.md 全部阶段 |
| 无 whisper | 加载 `setup-guide.md` |

### 双模式工作流 (v3.0)

Skill 自动检测项目资源，选择修复策略：

| 模式 | 触发条件 | 修复策略 | 核心原则 |
|------|---------|---------|---------|
| **text** | `原始字幕/` 目录有文件（有参考字幕） | 词典对照 + romaji_fixer + AI 审查 | 改错也能对照参考字幕验证 |
| **audio** | 无参考字幕，只有视频+音频 | VAD + Whisper 重录 or 删除 | **只看音频，不猜不填词表** |

`episode_workflow.py` 默认 `--mode auto`，自动检测 `原始字幕/` 目录：
- 有文件 → text 模式: `scan → fix → apply → diff`
- 无文件 → audio 模式: `scan → audio → apply → diff`

### 临时文件规则

**所有中间/临时文件统一放到项目 `temp/` 目录，禁止在项目根目录生成：**

```
项目/
├── temp/                  ← 所有临时文件（.gitignore 忽略）
│   ├── audio/             ← VAD 提取的音频片段 (.wav)
│   ├── whisper/           ← Whisper 原始输出 (.txt/.json)
│   ├── scans/             ← 扫描中间结果
│   └── fixes/             ← 修复预览 / diff 输出
├── AI审查后/              ← 工作目录（Git 管理）
├── 原始字幕/              ← 只读备份
└── reports/               ← 问题解决报告
```

脚本输出路径规范：
- `findings.json` → `temp/scans/findings.json`
- `fixes.json` → `temp/fixes/fixes.json`
- `delete_candidates.json` → `temp/scans/delete_candidates.json`
- `issues/` → `temp/scans/issues/`
- VAD 音频片段 → `temp/audio/`
- Whisper 原始输出 → `temp/whisper/`

## 工具架构：检测 → 审查 → 修复

```
检测脚本 (JSON) → Claude 审查 → fixes.json → apply_fixes.py → SRT
```

### 核心脚本速查

| 脚本 | 用途 | 模式 |
|------|------|------|
| `unified_scanner.py` | **[推荐]** 统一字符扫描 — 单次遍历检测所有源语言残留 | 通用 |
| `episode_workflow.py` | **[推荐]** 单集一键校对 — 自动检测模式 | 通用 |
| `romaji_fixer.py` | 统一修复生成 — 词典替换 + 噪声删除 | text |
| `build_project_dict.py` | 项目词典生成 — 从 findings 发现高频罗马字词 | text |
| `whisper_transcribe.py` | Tier 1 — 集群切片→whisper重转录 | audio |
| `whisper_full_episode.py` | Tier 2 — 整集转录+SRT对齐 | audio |
| `whisper_deep_fix.py` | Tier 3 — silencedetect拆分修复 | audio |
| `apply_review_fixes.py` | 审查清单→VAD打轴→SRT | 通用 |
| `apply_fixes.py` | fixes.json 批量应用 | 通用 |
| `check_progress.py` | 一键快速进度统计 | 通用 |
| `update_report.py` | 报告查询（用 `--summary`，禁止直接 cat） | 通用 |
| `clean_empty_cues.py` | 清理空白 cue | 通用 |

### 检测脚本速查

| # | 脚本 | 检测类型 | 适用条件 |
|---|------|------|----------|
| — | `unified_scanner.py` | **[新]** 字符层统一扫描 | **所有项目** |
| — | `romaji_fixer.py` | **[新]** 词典修复+删除 | **text 模式（有参考字幕）** |
| — | `episode_workflow.py` | **[新]** 单集工作流编排 | **所有项目** |
| 1 | `repeat_detect.py` | 卡死重复序列 | 所有项目 |
| 2 | `trad_to_simp_detect.py` | 繁体→简体 | 中文目标语言 |
| 6 | `interjection_detect.py` | 感叹词残留 | 翻译项目 |
| 7 | `names_detect.py` | Name字段扫描 | ASS only |
| 8 | `styles_detect.py` | 样式异常 | ASS only |
| 9 | `drawing_detect.py` | 绘图指令误译 | ASS only |
| 10 | `format_detect.py` | 固定格式变体 | 翻译项目 |
| 11 | `comment_detect.py` | Comment行残留 | ASS only |
| 12 | `garbled_detect.py` | 机翻幻觉（语义层） | 中文目标语言 |
| 13 | `oped_detect.py` | OP/ED异常 | ASS + 多样式 |
| 14 | `proper_noun_detect.py` | 专名变体 | 有参考字幕 |
| 15 | `oped_timecode_detect.py` | OP/ED时间码 | 所有项目 |

## 核心工作流

### 分支 A: 文本规则检测（所有项目必做）

**字符层** — 统一扫描，单次遍历：
```
A1. unified_scanner.py --target-dir <DIR> --output-findings findings.json
     → 检测所有乱码 cue（纯罗马字 / 混合行 / 噪声 / 幻觉 / 西里尔残留）
```

**修复路径按模式分叉：**

*text 模式（有参考字幕）：*
```
A2. build_project_dict.py → project_dict.json（首次，后续增量更新）
A3. romaji_fixer.py --findings findings.json --project-dict project_dict.json --output fixes.json
A4. Claude 审查 fixes.json → apply_fixes.py
```

*audio 模式（无参考字幕，只有音频）：*
```
A2. episode_workflow.py EP064 --mode audio
    → VAD 判断 → Whisper 重录 or 删除（不猜，不填词表）
```

**语义层** — 独立脚本，按项目特征选用：
```
A5. repeat_detect.py      — 卡死重复
A6. garbled_detect.py     — 机翻幻觉（仅中文目标语言）
A7. format_detect.py      — 固定格式变体（仅翻译项目）
A8. clean_empty_cues.py   — 清理空行
```

### 分支 B: Whisper 管线（有视频+whisper 时启用）

详见 `references/whisper-pipeline.md`。入口：
```
B1. unified_scanner.py 的 issues 输出 → per-episode issue JSONs
B2. whisper_transcribe.py     Tier 1: 集群切片重转录
B3. whisper_full_episode.py   Tier 2: 整集转录（碎片≥15条）
B4. whisper_deep_fix.py       Tier 3: silencedetect 拆分
B5. apply_review_fixes.py     审查清单→SRT
```

### 分支 C: 完整模式（有参考字幕时启用）

委托到 `references/full-mode.md`。text 模式下可用 romaji_fixer 做初步词典修复，再对照参考字幕精细校对。

### 分支 D: ASS 专用（目录含 .ass 文件时启用）

```
D1. names_detect.py / styles_detect.py / drawing_detect.py
D2. comment_detect.py / oped_detect.py
```

## 报告

**唯一报告**：`reports/问题解决报告.md`（禁止直接读取！用 `update_report.py --summary` 查询）

审查员工作文件：`reports/manual-review/review-checklist.md`（填写修正→`apply_review_fixes.py` 写入 SRT）

专有名词表：`reports/proper-nouns.md`（累积追加）

## 技术备忘

- ASS: `split(',', 9)` 防逗号破坏；跳过 Display 样式；先 strip `{...}` 再检测
- SRT: `utf-8-sig` BOM；无 Name/Style 字段
- 正则: glob 方括号用 `os.listdir` 替代；Windows 路径用正斜杠
- Whisper 参数: `-nth 0.6 -mc 0 -sns`（Tier 3 用 `-nth 0.3`）；WAV 无损管道
- 字符层 vs 语义层: 字符层 = 非目标语言**字符**混入（正则可检测）；语义层 = 目标语言**字符**正确但**含义**错误（需模式匹配/上下文判断）
