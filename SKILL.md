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
├── 参考字幕/          ← （可选）准确参考字幕，用于 text 模式对照
├── AI审查后/          ← 工作目录（Git 管理）
├── temp/              ← 所有临时文件（.gitignore）
│   ├── audio/         ← VAD 提取的音频片段
│   ├── whisper/       ← Whisper 原始输出
│   ├── scans/         ← 扫描中间结果
│   ├── translations/  ← 翻译后 SRT
│   └── compares/      ← 对照差异报告
└── reports/           ← 问题解决报告 + 审查清单
```

**铁律**：修改 SRT 前 `git add -A && git commit -m "备份：..."`；回滚 `cp 原始字幕/XXX.srt AI审查后/`

## 启动：项目感知模式匹配

| 项目特征 | 自动行为 |
|----------|---------|
| 只有目标字幕 | 分支 A（必需步骤） |
| +参考字幕 | +分支 C（对照验证翻译），加载 `references/full-mode.md` |
| +视频 + whisper | +分支 B（音频重转录），加载 `references/whisper-pipeline.md` |
| SRT only（无 .ass 文件） | 跳过 ASS 专用脚本 |
| 日语源语言 | 跳过 trad_to_simp、garbled_detect、interjection_detect |
| 无参考字幕 | audio 模式：VAD+Whisper，不猜 |
| 无 whisper | 加载 `setup-guide.md` |

## v4.0 架构：统一音频优先 + 翻译对照

```
                  ┌── 统一扫描 ────────────────┐
                  │ 只分 clean / garbled 两种   │
                  └────────────────────────────┘
                               │
                               ▼
                  ┌── VAD 判断 ─────────────────┐
                  │  有语音 → Whisper 重录       │
                  │  无语音 → 标记删除           │
                  └────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
               audio 模式            text 模式
               (无参考字幕)          (有参考字幕)
                    │                     │
                    ▼                     ▼
              apply → 完成        ┌── 翻译参考字幕 ──┐
                                  │ 百度翻译 API      │
                                  │ 参考字幕→日语     │
                                  └──────────────────┘
                                         │
                                         ▼
                                  ┌── 对照验证 ──────┐
                                  │ Whisper vs 翻译  │
                                  │ 一致→通过         │
                                  │ 不一致→人工审查   │
                                  └──────────────────┘
                                         │
                                         ▼
                                  apply → 完成
```

## 工作流模式

| 模式 | 触发条件 | 流水线 | 核心原则 |
|------|---------|--------|---------|
| **audio** | 无 `参考字幕/` 目录 | `scan → audio → apply → diff` | 音频是唯一真相来源 |
| **text** | `参考字幕/` 有文件 | `scan → audio → translate → compare → apply → diff` | 翻译对照验证 |

`episode_workflow.py` 默认 `--mode auto`，自动检测 `参考字幕/` 目录。

## 临时文件规则

**所有中间/临时文件统一放到项目 `temp/` 目录：**

```
temp/
├── audio/             ← VAD 提取的音频片段 (.wav)
├── whisper/           ← Whisper 原始输出 (.txt/.json)
├── scans/             ← 扫描中间结果 (findings.json, issues/)
├── translations/      ← 百度翻译后 SRT
└── compares/          ← 对照差异报告 (diff.json)
```

## 核心脚本速查

| 脚本 | 用途 | 模式 |
|------|------|------|
| `unified_scanner.py` | 统一字符扫描 — 检测 garbled cue | 通用 |
| `episode_workflow.py` | 单集一键校对 — 自动检测模式 | 通用 |
| `whisper_transcribe.py` | Tier 1 — 集群切片→whisper重转录 | 通用 |
| `whisper_full_episode.py` | Tier 2 — 整集转录+SRT对齐 | 通用 |
| `whisper_deep_fix.py` | Tier 3 — silencedetect拆分修复 | 通用 |
| `translate_srt.py` | **[新]** 百度翻译 API 批量翻译 SRT | text |
| `compare_srt.py` | **[新]** SRT 对照验证（Whisper vs 翻译） | text |
| `apply_review_fixes.py` | 审查清单→VAD打轴→SRT | 通用 |
| `apply_fixes.py` | fixes.json 批量应用 | 通用 |
| `check_progress.py` | 一键快速进度统计 | 通用 |
| `update_report.py` | 报告查询（用 `--summary`，禁止直接 cat） | 通用 |
| `clean_empty_cues.py` | 清理空白 cue | 通用 |

## 百度翻译 API 配置（text 模式需要）

```bash
# 1. 注册：https://fanyi-api.baidu.com/ → 开通通用翻译API（标准版，免费）
#    免费额度：200 万字符/月，1 QPS

# 2. 创建配置文件：
echo "BAIDU_APPID=你的APPID" > ~/.baidu_translate
echo "BAIDU_SECRET=你的密钥" >> ~/.baidu_translate

# 或设置环境变量：
export BAIDU_APPID=你的APPID
export BAIDU_SECRET=你的密钥

# 3. 测试翻译（dry-run 估算用量）：
python scripts/translate_srt.py 参考字幕/EP001.srt --to ja --output temp/translations/EP001.srt --dry-run
```

## 核心工作流

### 分支 A: 文本规则检测（所有项目必做）

```
A1. unified_scanner.py --target-dir <DIR> --output-findings findings.json
    → 检测所有 garbled cue（含拉丁/西里尔字符的 cue）
```

### 分支 B: Whisper 管线（有视频+whisper 时启用）

详见 `references/whisper-pipeline.md`。

```
B1. episode_workflow.py EP064 --step audio
    → VAD 判断 → Whisper Tier 1 重录 or 标记删除
B2. whisper_full_episode.py   Tier 2: 整集转录（碎片≥15条）
B3. whisper_deep_fix.py       Tier 3: silencedetect 拆分
```

### 分支 C: 翻译对照（有参考字幕时启用）

```
C1. translate_srt.py 参考字幕/EP001.srt --to ja --output temp/translations/EP001.srt
    → 百度翻译 API 批量翻译参考字幕→日语
C2. compare_srt.py AI审查后/EP001.srt temp/translations/EP001.srt
    → 按时间码对齐，计算文本相似度，生成差异报告
C3. 人工审查 flagged mismatches → apply_fixes.py
```

### 分支 D: ASS 专用（目录含 .ass 文件时启用）

```
D1. names_detect.py / styles_detect.py / drawing_detect.py
D2. comment_detect.py / oped_detect.py
```

## 报告

**唯一报告**：`reports/问题解决报告.md`（禁止直接读取！用 `update_report.py --summary` 查询）

审查员工作文件：`reports/manual-review/review-checklist.md`（填写修正→`apply_review_fixes.py` 写入 SRT）

## 技术备忘

- ASS: `split(',', 9)` 防逗号破坏；跳过 Display 样式；先 strip `{...}` 再检测
- SRT: `utf-8-sig` BOM；无 Name/Style 字段
- 正则: glob 方括号用 `os.listdir` 替代；Windows 路径用正斜杠
- Whisper 参数: `-nth 0.6 -mc 0`（Tier 3 用 `-nth 0.3`）；WAV 无损管道；不加 `-sns`（制造幻觉）
- 百度翻译: MD5 签名认证，1 QPS 限速，200 万字符/月免费，支持 auto→ja
- 字符层 vs 语义层: 字符层 = 非目标语言**字符**混入（正则可检测）；语义层 = 字符正确但**含义**错误
