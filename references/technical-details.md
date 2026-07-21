# 字幕校对 Skill — 技术实现文档

> 本文档描述各脚本的实现细节、算法原理和参数含义。
> 用户向概览见 [SKILL.md](./SKILL.md)。

---

## 目录

1. [脚本总览](#1-脚本总览)
2. [Whisper 管线](#2-whisper-管线)
3. [VAD 语音检测](#3-vad-语音检测)
4. [字符扫描 (unified_scanner)](#4-字符扫描-unified_scanner)
5. [置信度系统](#5-置信度系统)
6. [翻译对照](#6-翻译对照)
7. [专名统一](#7-专名统一)
8. [批量修复 (apply_fixes)](#8-批量修复-apply_fixes)
9. [报告系统 (update_report)](#9-报告系统-update_report)
10. [AI 审查层](#10-ai-审查层)
11. [人工交付](#11-人工交付)
12. [ASS 格式修补](#12-ass-格式修补)

---

## 1. 脚本总览

### 架构

```
scripts/
├── run_all.py                     ← 批量编排器，逐集调 episode_workflow
├── scan/unified_scanner.py     ← 单次遍历：乱码检测 + 重复检测 + 术语收集
├── fix/
│   ├── fix_orchestrator.py           ← 统一修复模块：参考字幕 → Whisper → 人工
│   ├── episode_workflow.py           ← 单集编排器（委托给 Fixer）
│   ├── whisper_pipeline.py           ← Whisper Tier 1/2 重转录
│   ├── translate_srt.py              ← 百度翻译 SRT（text 模式）
│   └── compare_srt.py                ← 时间码对齐 + 文本相似度比对
├── nouns/
│   ├── noun_checker.py            ← 专名一致性 + 跨集 OP/ED 统一
│   └── build_glossary.py          ← 术语表自动生成
├── apply/apply_fixes.py        ← 批量修复：繁→简 + 翻译腔 + fixes 应用
├── ass/ass_repair.py           ← ASS 格式修补（5 种检查）
├── utils/
│   ├── check_progress.py          ← 进度统计
│   ├── update_report.py           ← 问题解决报告 6 层读写
│   └── clean_empty_cues.py        ← 清理空白 cue
└── lib/
    ├── srt_utils.py               ← SRT 解析/写回
    ├── ass_utils.py               ← ASS 解析/写回
    └── whisper_utils.py           ← Whisper CLI 调用 + ffmpeg + VAD + 置信度
```

### 脚本间调用关系

```
run_all.py
  ├─→ unified_scanner.py           (1次，全量扫描)
  ├─→ episode_workflow.py EPxxx    (逐集，subprocess)
  │     ├─→ whisper_pipeline.py    (音频修复)
  │     │     └─→ whisper-cli.exe  (Whisper 推理)
  │     ├─→ translate_srt.py       (text 模式)
  │     ├─→ compare_srt.py         (text 模式)
  │     └─→ noun_checker.py        (专名审查)
  ├─→ noun_checker.py              (1次，全量专名)
  └─→ apply_fixes.py              (1次，批量应用)
```

---

## 2. Whisper 管线

### 核心调用

`whisper_utils.py:run_whisper()` 封装 whisper.cpp CLI：

```bash
whisper-cli.exe -m <model> -f <audio.wav> -l ja \
  -t 8 -p 2 -bs 5 -bo 8 -oj -nth 0.6 -mc 0 --print-progress
```

| 参数 | 值 | 说明 |
|------|----|------|
| `-nth` | 0.6 | 无声阈值，越低越敏感 |
| `-mc` | 0 | 跨段上下文，0=禁用（避免幻觉传播） |
| `-sns` | 默认不加 | 非语音 token 抑制——研究证实会在音乐/静音段制造幻觉 |

### Tier 1: 拼接式片段修复

**策略**：乱码 cue ≤ 15 条时使用。提取所有乱码 cluster 音频 → 拼接为一个 WAV → 一次 Whisper → 按偏移量拆回。

```
1. build_clusters(): 按 MAX_CLUSTER_GAP (60s) 聚类乱码 cue
2. extract_audio_wav(): ffmpeg 提取每个 cluster 的 WAV
3. _concat_wavs(): 拼接为 combined.wav（段间 2s 静音）
4. run_whisper(): 一次推理
5. 按累计偏移量拆回各 cluster → match_whisper_to_cues()
```

**拼接实现**（`_concat_wavs()`）：

```python
# 用 wave 模块逐段读取 PCM 帧 → 拼接 → 写入新 WAV
offsets = [(cluster_idx, combined_start, combined_end, original_ss), ...]
# 夹入 silence_s=2.0 秒的零字节作为段间分隔

# 映射回原始时间码：
# Whisper 段在 combined 文件中的时间 → 减去 combined_start → 得到 cluster 内偏移
# match_whisper_to_cues() 再加回 cluster['ss'] → 原始视频时间
```

**性能**：1 次模型加载 + 1 次推理，和 cluster 数量无关。

### Tier 2: 整集重转录

**触发条件**：乱码 > 15 条（`UPGRADE_THRESHOLD`），或在 Tier 1 后自动升级。

提取整集音频（26 分钟）→ 一次 Whisper → `align_and_fix()` 逐 cue 时间码对齐。

**对齐算法**：Whisper 段与 SRT cue 的时间重叠比 ≥ 0.3 → 采纳；≥ 0.5 → confidence=high。

### BGM 去除 (demucs)

`whisper_utils.py:separate_vocals()` 调用 htdemucs 做音源分离：

```bash
python -m demucs --two-stems=vocals -o <out_dir> <audio.wav>
# 输出: <out_dir>/htdemucs/<name>/vocals.wav
```

**默认启用**。拼接模式下对全量 combined 音频跑一次（非逐段），避免 N 次模型加载。

---

## 3. VAD 语音检测

`whisper_pipeline.py:vad_delete_nonspeech()` 两级删除策略：

### Tier A: 内容标记删除
已知非对话标记（`[音楽][拍手][笑い]` 等 20+ 种）→ 无条件删除。这些是编辑注记，不是台词。

### Tier B: 音频检测删除
文本 cue 但 WebRTC VAD 检测到**零语音重叠**且时长 < 3s → 删除。
WebRTC VAD 用高斯混合模型区分语音/音乐，比 ffmpeg silencedetect（仅检测音量）准确。

### Fallback
`webrtcvad` 库不可用时 → `silencedetect` fallback。

---

## 4. 字符扫描 (unified_scanner)

`scan/unified_scanner.py`：单次 SRT 遍历完成：

| 检测项 | 方法 | 输出字段 |
|--------|------|---------|
| **乱码字符** | 拉丁字母混入日文行 → `is_garbled` | `garbled_cues[]` |
| **卡死重复** | N-gram 检测连续相同序列 | `repeats[]` |
| **术语收集** | 片假名序列 + 敬称模式提取 | `--build-glossary` → proper-nouns.md |

输出 `findings.json` 供后续层读取，避免重复扫描 193 个 SRT。

---

## 5. 置信度系统

### Whisper 三项指标

`whisper.cpp` JSON 输出逐段提供：

| 指标 | 来源 | 含义 | AI 审查阈值 |
|------|------|------|-----------|
| `avg_logprob` | token 平均对数概率 | 模型对转录的确信度 | < -1.0 |
| `no_speech_prob` | `<|nospeech|>` token 概率 | 该段是否静音 | > 0.4 |
| `compression_ratio` | gzip 压缩比 | 重复文本→幻觉标志 | > 2.0 |

### 三道防线过滤

`whisper_utils.py:filter_low_confidence()` 社区推荐标准：
1. `no_speech_prob > 0.6` → 丢弃
2. `avg_logprob < -1.5` → 丢弃
3. `compression_ratio > 2.4` → 丢弃

### 管线集成

Tier 1/2 修复时，Whisper 置信度从 whisper segment **透传到 fix 条目**（`avg_logprob`/`no_speech_prob`/`compression_ratio` 字段）。

`episode_workflow.py:step_apply()` 分流：
- 三项指标全部健康 → L2 ✅ 直接应用
- 任一触发 AI 审查阈值 → L2.5 ⬜ AI 审查
- confidence='none' → L6 ⬜ 人工交付

---

## 6. 翻译对照

### translate_srt.py

调用百度翻译 API，将参考字幕翻译为目标语言：
- 认证：MD5 签名（appid + query + salt + key）
- 限速：1 QPS，200 万字符/月免费
- 环境变量 `BAIDU_APPID`、`BAIDU_KEY`
- 可选 HTTP 代理：`BAIDU_ENDPOINT`

### compare_srt.py

时间码对齐后逐行比对：
- 按时间码匹配目标 SRT 和翻译后 SRT 的对应行
- 计算文本相似度（编辑距离比）
- 输出差异报告 → Claude 审查 → fixes.json

---

## 7. 专名统一

### noun_checker.py

语境感知匹配，避免误报：

- **敬称语境**：`〜さん/くん/様/ちゃん` → 大概率是专名
- **呼唤语境**：`おい〜`、`〜!` → 可能是角色名
- **介绍语境**：`〜です/だ/である` → 可能是人名
- **排除**：常用外来语不触发（`ゲーム`、`ロボット` 等）

跨集 OP/ED 一致性（`--oped`）：
1. 按时码分桶（OP/ED 固定时间窗口）
2. 相同位置的文本变体 → 发现拼写差异
3. 最高频形式作为规范 → 其他集统一

### build_glossary.py

从 `findings.json` 的术语频率数据自动生成 proper-nouns.md：
- 片假名序列 → 候选术语
- 跨集频率统计 → 去重排序
- 智能分组（同一词根的变体合并）

---

## 8. 批量修复 (apply_fixes)

### 内置转换

| 转换 | 条件 | 实现 |
|------|------|------|
| 繁→简 | `--lang zh` 自动 | `str.maketrans()` 150+ 字符映射 |
| 翻译腔去机械化 | `--lang zh` | 15 条 EN→ZH 正则替换 |
| 空白 cue 清理 | 自动 | 删除 text 为空的 cue |

### fixes.json 格式

```json
[
  {"action": "replace_global", "original": "...", "replacement": "...", "note": "..."},
  {"action": "replace_global_regex", "pattern": "...", "replacement": "...", "note": "..."},
  {"action": "replace_text", "file": "...", "line": N, "replacement": "..."},
  {"action": "delete_line", "file": "...", "line": N},
  {"action": "delete_style", "style": "..."}
]
```

### 人工审查清单应用

支持解析 `review-checklist.md`（`|` 分隔格式）→ 提取修正文本 → 应用到 SRT。

---

## 9. 报告系统 (update_report)

### 7 层格式

```
## 第1层: 字符扫描
## 第2层: 语义修复
## 第2.5层: AI置信度审查
## 第3层: 专名统一
## 第3.5层: AI专名抽样审查
## 第4层: 批量修复
## 第5层: 格式修补 [ASS only]
## 第6层: 人工交付
```

### API

```python
from utils.update_report import read_report, write_report, upsert_entries, update_entry_status

data = read_report(path)                          # → {layer_id: [entries]}
upsert_entries(path, step='2', entries=[...])     # 批量 upsert（按集+时间去重）
update_entry_status(path, step='6', ep=..., ...)  # 单条状态更新
```

### 兼容性

`read_report()` 自动识别旧格式（`## 步骤N:`）并映射到新层号（`STEP_TO_LAYER`）。

---

## 10. AI 审查层

### L2.5: AI 置信度审查

**数据流**：`step_apply()` → 筛选低置信度 fixed 条目 → `temp/scans/{EP}_ai_review.json`

**AI 审查条目格式**：
```json
{
  "start": "00:02:00.490",
  "original": "garbled text",
  "replacement": "Whisper's guess",
  "avg_logprob": -1.8,
  "flag_reasons": ["avg_logprob=-1.80"],
  "context_before": "previous cue text",
  "context_after": "next cue text"
}
```

**审查方式**：`python episode_workflow.py EP064 --step ai-review`

三种结论：
- ✅ 保留 → 写入 L2 报告
- ✏️ 修正 → 给出文本，走 apply_fixes
- ⬜ 升级 → L6 人工交付

### L3.5: AI 专名抽样审查

noun_checker 输出中 `unknown/mismatch > 0` → 抽样 top 20 高频候选 → AI 判断是专名还是普通词汇 → fixes.json 或 proper-nouns.md。

---

## 11. 错误修复（统一 Layer 2）

### fix_orchestrator.py (Fixer)

`Fixer` 类统一了原来的 Layer 2（Whisper 修复）和 Layer 6（人工审查），三级降级：

1. **参考字幕翻译对照**（`fix_by_reference`）— 有参考字幕时优先，比较+采纳
2. **Whisper 人声转录**（`fix_by_whisper`）— 提取音频（上下文包裹）+ Tier 1/2 转录
3. **人工审查**（`review` + `apply`）— checklist 生成 + 人工修正应用

所有修正通过统一的 SRT 写入 + 报告更新路径。SRT 是唯一真相源。


功能已迁移到 `fix_orchestrator.py`。保留文件但不维护。

输出：`reports/manual-review/`
- `EPxxx_HH-MM-SS-sss.mp4` — 视频片段（上下文包裹，ffmpeg）
- `EPxxx_checklist.md` — 审查清单模板（version: 2）

视频片段参数：libx264 CRF 28，AAC 64k，640px 宽，faststart。

---

## 12. ASS 格式修补

`ass/ass_repair.py --check all` 覆盖 5 种检查：

| 检查 | 实现 |
|------|------|
| `names` | Name 字段按语言分类，标记非目标语言名称 |
| `styles` | 样式使用统计 → 识别译者署名样式 |
| `drawing` | `{\pN}` 绘图指令中的文字误译 |
| `comment` | Comment 行外语残留 |
| `oped` | OP/ED 多样式轨文本对比（需 `--oped-config`） |

ASS 文本解析：`split(',', 9)` 防逗号破坏；跳过 Display 样式。

### SRT vs ASS 差异

| 项 | SRT | ASS |
|----|-----|-----|
| 编码 | `utf-8-sig` BOM | `utf-8` |
| Name/Style 字段 | 无 | 有 |
| 文本分隔符 | `\n` | `\N` |
| 特效层 | 无 | `{\k...}` 等 |
