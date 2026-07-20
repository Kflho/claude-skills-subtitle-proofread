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

## 启动：模式自动匹配

| 用户资源 | 加载子文档 | 说明 |
|----------|----------|------|
| 只有目标字幕 | 本文件（基础模式） | 规则检测 + Claude 推断 |
| +参考字幕 | + `full-mode.md` | 对照验证翻译 |
| +AI 听译来源 | + `ai-asr-fix.md` | ASR 罗马字/乱码修复 |
| +视频 + whisper | + `whisper-stage.md` | 音频重转录修复 |
| 无 whisper | + `setup-guide.md` | 首次安装引导 |

## 工具架构：检测 → 审查 → 修复

```
检测脚本 (JSON) → Claude 审查 → fixes.json → apply_fixes.py → SRT
```

脚本详情见 `script-templates.md`（按需加载）。

### 核心脚本速查

| 脚本 | 用途 |
|------|------|
| `whisper_transcribe.py` | Tier 1 — 集群切片→whisper重转录→审查清单 |
| `whisper_full_episode.py` | Tier 2 — 整集转录+SRT对齐 |
| `whisper_deep_fix.py` | Tier 3 — silencedetect拆分修复 |
| `apply_review_fixes.py` | 审查清单→VAD打轴→SRT（加 `--update-report`） |
| `apply_fixes.py` | fixes.json 批量应用（加 `--log-to-report --step N`） |
| `update_report.py` | 报告查询（用 `--summary`，禁止直接 cat 报告） |

### 检测脚本速查（16个）

| # | 脚本 | 检测类型 |
|---|------|------|
| 1 | `repeat_detect.py` | 卡死重复序列 |
| 2 | `trad_to_simp_detect.py` | 繁体→简体 |
| 3 | `bilingual_detect.py` | 双语混合行 |
| 4 | `source_lang_detect.py` | 纯源语言行 |
| 5 | `source_char_detect.py` | 多语言字符残留 |
| 6 | `interjection_detect.py` | 感叹词残留 |
| 7 | `names_detect.py` | Name字段扫描 |
| 8 | `styles_detect.py` | 样式异常 |
| 9 | `drawing_detect.py` | 绘图指令误译 |
| 10 | `format_detect.py` | 固定格式变体 |
| 11 | `comment_detect.py` | Comment行残留 |
| 12 | `garbled_detect.py` | 机翻幻觉 |
| 13 | `oped_detect.py` | OP/ED异常 |
| 14 | `proper_noun_detect.py` | 专名变体 |
| 15 | `oped_timecode_detect.py` | OP/ED时间码 |
| 16 | `generate_romaji_fixes.py` | 罗马字→假名 |

## 基础工作流（独立模式）

1. 卡死重复 → `repeat_detect.py`
2. 繁→简 → `trad_to_simp_detect.py --auto`
3. 双语混合 → `bilingual_detect.py`
4. 纯源语言 → `source_lang_detect.py`
5. 字符残留 → `source_char_detect.py`
6. 感叹词 → `interjection_detect.py`
7. Name字段 → `names_detect.py`
8. Comment行 → `comment_detect.py`
9. 样式 → `styles_detect.py`
10. 绘图 → `drawing_detect.py`
11. 格式 → `format_detect.py`
12. 残留检查 → 重跑 1-8 清零

## 报告

**唯一报告**：`reports/问题解决报告.md`（16步分类，禁止直接读取！用 `update_report.py --summary` 查询）

审查员工作文件：`reports/manual-review/review-checklist.md`（填写修正→`apply_review_fixes.py` 写入 SRT）

专有名词表：`reports/proper-nouns.md`（累积追加）

## 技术备忘

- ASS: `split(',', 9)` 防逗号破坏；跳过 Display 样式；先 strip `{...}` 再检测
- SRT: `utf-8-sig` BOM；无 Name/Style 字段；Comment 行用 `comment_detect.py` 单独扫
- 正则: glob 方括号用 `os.listdir` 替代；Windows 路径用正斜杠
- Whisper 参数: `-nth 0.6 -mc 0 -sns`（Tier 3 用 `-nth 0.3`）；WAV 无损管道
