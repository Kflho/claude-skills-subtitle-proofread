# 字幕校对 Skill — 技术实现文档

> 本文档描述各脚本的实现细节、算法原理和参数含义。
> 用户向概览见 [SKILL.md](../SKILL.md)。开发者速览见 [dev/architecture.md](../dev/architecture.md)。

---

## 目录

1. [脚本总览](#1-脚本总览)
2. [数据流：一集从头到尾](#2-数据流一集从头到尾)
3. [Whisper 管线](#3-whisper-管线)
4. [VAD 语音检测](#4-vad-语音检测)
5. [字符扫描 (unified_scanner)](#5-字符扫描-unified_scanner)
6. [置信度系统](#6-置信度系统)
7. [翻译对照](#7-翻译对照)
8. [专名统一](#8-专名统一)
9. [批量修复 (apply_fixes)](#9-批量修复-apply_fixes)
10. [报告系统 (update_report)](#10-报告系统-update_report)
11. [AI 审查层](#11-ai-审查层)
12. [人工交付](#12-人工交付)
13. [ASS 格式修补](#13-ass-格式修补)
14. [已知技术债务](#14-已知技术债务)

---

## 1. 脚本总览

### 架构

```
scripts/
├── run_all.py                     ← 批量编排器，逐集调 episode_workflow
├── scan/unified_scanner.py        ← 单次遍历：乱码检测 + 重复检测 + 术语收集
├── fix/
│   ├── fix_orchestrator.py        ← 统一修复模块（Fixer 类）：参考→Whisper→auto_triage
│   ├── episode_workflow.py        ← 单集编排器（大部分逻辑已迁移到 Fixer）
│   ├── whisper_pipeline.py        ← Whisper Tier 1 拼接 / Tier 2 整集 + VAD + build_clusters
│   ├── translate_srt.py           ← 百度翻译 SRT（text 模式专用）
│   └── compare_srt.py             ← 时间码对齐 + 文本相似度比对
├── nouns/
│   ├── noun_checker.py            ← 专名一致性 + 跨集 OP/ED 统一
│   ├── auto_classify.py           ← 专名自动分类（Jamdict + 规则，减少 AI 审查量）
│   └── build_glossary.py          ← 术语表自动生成（含 Jamdict 过滤）
├── apply/apply_fixes.py           ← 批量修复：繁→简 + 翻译腔 + fixes + review checklist
├── ass/ass_repair.py              ← ASS 格式修补（SRT 项目跳过）
├── utils/
│   ├── update_report.py           ← 问题解决报告 6 层读写
│   └── clean_empty_cues.py        ← 清理空白 cue
└── lib/
    ├── srt_utils.py               ← SRT 解析/写回（行列表模型）
    ├── ass_utils.py               ← ASS 解析/写回（兼容 SRT）
    ├── whisper_utils.py           ← Whisper CLI + ffmpeg + VAD + 分类 + 置信度（cue 字典模型）
    ├── project_utils.py           ← 模式检测 + 文件查找 + git 备份
    ├── japanese_utils.py          ← 日语常量：常见词、敬称、非对话标记
    └── chinese_utils.py           ← 繁→简映射表 + 拼音声调
```

### 关键区别：两套 SRT 数据模型

| 模块 | 数据模型 | 用途 |
|------|---------|------|
| `srt_utils.py` | 行列表 (`list[str]`)，ASS 兼容 dict | 文件读写、逐行编辑、`apply_fixes.py` |
| `whisper_utils.py` | cue 字典列表（`start_s`, `end_s`, `text`...） | 时间码运算、乱码分类、Whisper 管线 |

两者通过 `whisper_utils.parse_srt()` 桥接：内部调 `srt_utils.parse_srt_cue()` 再包装为 cue dict。

**SRT 写入也有两个入口**：`whisper_utils.write_srt(cues)` 接受 cue 列表；`srt_utils.write_srt_file(lines)` 接受行列表。调用方根据持有哪种模型选择。

### 脚本间调用关系（6 层全流程）

```
run_all.py (唯一入口)
  ├─→ unified_scanner.py              L1: 全量扫描 → findings.json
  ├─→ episode_workflow.py EPxxx       L2: 逐集（subprocess）
  │     └─→ Fixer.run_auto()          (cascading: ref → Whisper → human)
  │           ├─ fix_by_reference()    → translate_srt.py + compare_srt.py
  │           ├─ fix_by_whisper()      → whisper_pipeline.py → whisper-cli.exe
  │           ├─ review_ai()           L2.5: AI 短碎片清单
  │           └─ review()              L6: 人工审查清单 + 视频片段
  ├─→ step_nouns()                    L3: noun_checker + auto_classify
  ├─→ step_apply_all()                L4: apply_fixes（收集所有 fixes 一次应用）
  ├─→ step_ass_repair()               L5: ASS only → 跳过
  └─→ step_deliver()                  L6: 统一审查清单生成/应用
```

---

## 2. 数据流：一集从头到尾

> **目标**：清空上下文后，看这一节就能理解每一集的数据怎么流动。

### SRT 文件流转

```
原始字幕/EP001.srt          ← 只读备份（git 管理，永不改）
        │
        ▼  (unified_scanner 扫描)
AI审查后/EP001.srt          ← 工作目录（git 管理，唯一真相源）
        │
        ▼  (Fixer.fix_by_whisper 修复乱码 cue)
AI审查后/EP001.srt          ← 原地修改（git commit 前备份）
        │
        ▼  (apply_fixes 批量应用)
AI审查后/EP001.srt          ← 原地修改（繁→简、翻译腔、全局替换）
```

### 检测 → 修复 → 报告 链路

```
unified_scanner (L1)
  │  扫描 AI审查后/*.srt
  │  对每个 cue 调 classify_garbled_text()
  │  输出 findings.json → per_episode_issues[EP001] = [乱码 cue 列表]
  ▼
Fixer.run_auto() (L2)
  │  读 findings.json → 知道哪些集有乱码
  │  parse_srt() → 重新 classify_garbled_text()（幂等，不会重复标记已修复的）
  │  build_clusters() → 归并相邻乱码 cue（MAX_CLUSTER_GAP=60s）
  │  Tier 1/2 Whisper → match_whisper_to_cues()
  │  auto_triage: looks_like_plausible_japanese() → 分诊
  │  ├── 可读 → 直接写 SRT + 报告 L2 ✅
  │  ├── 短碎片 → 报告 L2.5 ⬜（等 AI 补全）
  │  ├── 专名模式 → 报告 L3 ⬜（等 noun_checker）
  │  └── 长乱码 → 报告 L6 ⬜（等人工）
  ▼
noun_checker + auto_classify (L3)
  │  读 proper-nouns.md 专名表 → 匹配/发现变体
  │  auto_classify(Jamdict+规则) → ACCEPT/REJECT/NEEDS_AI
  │  NEEDS_AI → L3.5，等 AI 判断
  ▼
apply_fixes (L4)
  │  收集所有 fixes（L3 auto_accepted + AI review + OP/ED）
  │  一次写入所有 SRT
  └─→ 问题解决报告.md 更新各层状态
```

### 关键函数调用链

```
parse_srt(path, mark_garbled=True)
  └─→ srt_utils.parse_srt_cue()          ← 行列表 → ASS 兼容 dict
  └─→ classify_garbled_text(text, lang)  ← 统一乱码判断（唯一检测源）
  └─→ to_seconds()                        ← 时间码 → 浮点秒数

build_clusters(cues)
  └─→ 过滤 garbled_cues（只取 is_garbled=True 的）
  └─→ 按 60s 间隔分组
  └─→ 每组向左/右扩展到最近的 clean cue

match_whisper_to_cues(whisper_segs, cluster, offset)
  └─→ Round 1: 时间重叠匹配
  └─→ Round 2: ±3s 宽窗口重试（_is_context_text 过滤左右上下文）
  └─→ 未匹配 → confidence='none' → L6

looks_like_plausible_japanese(text, lang)
  └─→ 可读性优先判断（比 classify_garbled_text 更宽松）
  └─→ True → 直接保留（不管 Whisper 置信度）
```

### auto_triage 分诊决策树

```
Whisper 输出 replacement
  │
  ├─ confidence='none' 或 无 replacement
  │   └─→ L6 人工（无音频输出也路由到这里）
  │
  ├─ looks_like_plausible_japanese(replacement) → True
  │   └─→ L2 ✅ 直接写入 SRT
  │
  └─ 不可读
      ├─ is_proper_noun_pattern(original) → True
      │   └─→ L3 专名审查
      ├─ is_short_garbled_fragment(replacement) → True
      │   └─→ L2.5 AI 上下文补全
      └─ 其余
          └─→ L6 人工
```

---

## 3. Whisper 管线

### 核心调用

`whisper_utils.py:run_whisper()` → 内部委托给 `whisper_backends.py:transcribe()` 统一接口。

支持三种后端：

| 后端 | 调用方式 | 关键参数 |
|------|---------|---------|
| whisper.cpp | CLI: `whisper-cli -m ... -f ...` | `-nth 0.6`, `-mc 0`, `-bs 5`, `-bo 8` |
| faster-whisper | Python API: `WhisperModel.transcribe()` | `beam_size=5`, `best_of=8`, `vad_filter=True` |
| openai-whisper | Python API: `whisper.transcribe()` | `beam_size=5`, `best_of=8` |

whisper.cpp CLI 示例：

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

### 数据流（改进后）

```
build_glossary.py                    ← 从 findings.json 生成词表
  │  JMdict 激进过滤: 在 JMdict → 普通词 → 删 (不管 JMnedict)
  │  COMMON_KANJI frozenset: 硬覆盖 (fallback + 已知常见词)
  └─→ proper-nouns.md

auto_clean_glossary.py               ← 自动清理漏网之鱼
  │  JMdict lookup + 启发式规则:
  │    · 动词词干 (着替/見捨/怒鳴…)
  │    · 时间碎片 (時間後/日前/万年後…)
  │    · 修饰语片段 (一番大/全部聞…)
  │    · 代词/后缀片段 (僕行/君僕…)
  │  KEEP: 姓氏/地名/动画特有概念 → 不误删
  └─→ COMMON_KANJI 自动扩充 → 重新生成 clean proper-nouns.md

noun_checker.py                      ← 用 clean 词表扫描 SRT
auto_classify.py                     ← ACCEPT/REJECT/NEEDS_AI
```

### build_glossary.py

过滤策略（三级）：

| 层级 | 机制 | 说明 |
|------|------|------|
| Tier 1 | `COMMON_KANJI` / `COMMON_KATAKANA` frozensets | 硬覆盖，始终生效 |
| Tier 2 | Jamdict (JMdict) | 在 JMdict → 普通词 → 删 |
| Tier 3 | 启发式模式匹配 | 动词词干/时间碎片/修饰语/代词片段 → 删 |

激进策略原理：
- JMnedict 包含大量稀有姓氏（如「世紀」「戦争」也是姓）
- 用户只关心中人名 + 动画特有概念（万馬力/電子相撲…）
- 日本/東京/火星 等现实概念也不需要追踪
- → 在 JMdict 就删，不查 JMnedict

### auto_clean_glossary.py

脚本启发式粗筛 — 自动化原本需要 3-5 轮人工肉眼扫描的循环。

```bash
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md          # dry-run
python nouns/auto_clean_glossary.py --glossary reports/proper-nouns.md --apply  # 自动清理
```

启发式规则：
- JMdict 查找（最可靠）
- 动词词干结尾（着替/見捨/怒鳴…）
- 时间/数字碎片（時間後/日前/万年後…）
- 修饰语片段（一番大/全部聞…）
- KEEP 保护：姓氏/地名模式 → 不误删

### L3.2 AI 词库审查

脚本粗筛后仍有 ~150 条，其中约 5-10% 是启发式无法覆盖的普通名词（语义判断）。

Claude 审查流程：
1. 读取 `proper-nouns.md` 汉字复合词表
2. 对每条判断：是人名？动画特有概念？还是普通名词？
3. 普通名词 → 加入 COMMON_KANJI
4. 重新生成 clean 表

此步骤 token 消耗低（~150 条目 × 简单分类），但效果显著。

### noun_checker.py

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

### 6+1 层格式

```
## 第1层: 字符扫描
## 第2层: 错误修复
## 第2.5层: AI短碎片补全
## 第3层: 专名统一
## 第3.5层: AI专名审查
## 第4层: 批量修复
## 第5层: 格式修补 [ASS only]
## 第6层: 人工审查
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

### L2.5: AI 短碎片补全

**触发**：VAD有人声 + Whisper输出不可读 + 短碎片(≤5拉丁字符) + 非专名。

**数据流**：`fix_by_whisper()` → auto_triage → `looks_like_plausible_japanese()` → 短碎片 → `review_ai()` → checklist

**AI 审查条目格式**（复用现有 checklist，`apply()` 自动解析）：
```markdown
EP003 | 00:25:32.160 ~
来源: short garbled fragment
残留: pa n
Whisper尝试: pants
上文: xxx  |  yyy
下文: zzz
修正:
```

AI 看上下文推测，填入「修正:」。不确定则留空。

### L3.5: AI 专名审查

noun_checker → auto_classify（Jamdict+规则）→ NEEDS_AI 候选项 → AI 判断 → fixes.json 或 proper-nouns.md。

**AI 不读名词表全文**，只收候选项 + 所在 cue 上下文。

---

## 12. 人工交付

### 统一审查清单

`run_all.py:step_deliver()` 统一入口，两种模式：

1. **生成模式**（默认）：收集 L6 ⬜ 条目 → 按集分组 → 输出 `reports/manual-review/checklist.md`（version: 3 unified）
2. **应用模式**（`--apply-checklist`）：解析 checklist → 逐集调 `Fixer.apply()` → VAD 时间对齐 → 写入 SRT

⚠️ **兼容性陷阱**：`run_all.py` 使用 v3 unified 格式（`## EP001` header 分组），向后兼容 v2 per-ep 格式。`apply_fixes.py --review` 使用另一种旧格式解析器。**不要通过 `apply_fixes.py` 应用 unified checklist — 它无法解析。**

### Fixer.apply() VAD 时间对齐

人工填入的正确台词通过 VAD 找到精确起止时间：
- VAD 1 段 → 用 VAD 边界替换 cue start/end
- VAD N≠1 段 → fallback：保持原边界，只替换 text
- VAD 0 段 → 标记无人声，建议删除
- 无 VAD → fallback：保持原边界替换

### 视频片段

`Fixer.review()` 调用 ffmpeg 提取视频 clip 供人工判断（口型/场景）。参数：libx264 CRF 28，AAC 64k，640px 宽，faststart。

**VAD 感知切片 v2**（2026-07-21 改进）：

| 改进 | 旧行为 | 新行为 |
|------|--------|--------|
| 聚类策略 | `build_clusters()` 60s gap + 扩展到相邻 clean cue → 片段时间过长 | 独立 `_build_review_clusters()` 8s tight gap，VAD 边界 |
| 静音截断 | 固定 ±5s padding → 大量 30s 纯音乐 clip | VAD 检测前后静音 >5s → 截断到 5s |
| 噪音过滤 | 无 — 所有乱码 cue 都生成 clip | 零 VAD 语音 → 直接砍掉；VAD <1.0s + 残留 ≤5 字母 → 砍掉 |
| 对话截断 | 固定 padding 不保证完整台词 | 扩展到包含全部 VAD 语音段 → 对话不截断 |
| 审查清单 | 重复生成双份（flat + per-ep） | 仅 per-ep 文件夹版（`reports/manual-review/EPxxx/checklist.md`） |

**"大胆砍"策略**：要么砍，要么送审，无中间态。
1. VAD 在乱码时间段零语音 → 自动标记报告 ✅，清单不出现
2. VAD 语音 <1.0s 且残留 ≤5 字母 → 同上
3. 其余 → 生成 clip，干净清单条目，无 VAD 备注/警告

---

## 13. ASS 格式修补

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

---

## 14. 已知技术债务

> **目的**：记录当前设计中的已知问题，防止清空上下文后踩坑。
> **最后更新**：2026-07-21
> ✅ = 已修复  |  ⚠️ = 仍存在  |  🟢 = 设计如此，非债务

### 14.1 ✅ 两套 checklist 解析器 — 已删除

旧版 `apply_fixes.py:_parse_review_checklist()` 已删除，`--review` 标志改为报错并指引用户使用 `run_all.py --apply-checklist`。
统一 checklist 只能通过 `run_all.py --apply-checklist` 应用。

### 14.2 ✅ `step_nouns()` god function — 已拆分

`run_all.py:step_nouns()` 拆为：
- `step_nouns()` — 编排 OP/ED + noun table check
- `_step_noun_classify()` — 运行 noun_checker + auto_classify subprocess
- `_apply_classified_results()` — 处理分类结果（accepted/rejected/needs_ai）

### 14.3 ✅ `step_deliver()` 双模式 — 已拆分

拆为：
- `step_deliver()` — 只生成 unified checklist
- `step_apply_checklist()` — 只解析并应用 checklist

### 14.4 ✅ `episode_workflow.py` 遗留兼容代码 — 已清理

`step_apply()` 移除 legacy list 处理路径（~100 行），简化到只处理 `FixReport`。
同时清理了 `_run_pipeline` 中对应的 `hasattr` 兼容检查。

### 14.5 ✅ SKILL.md 架构图 — 已更新

补全了 `lib/` 下所有 6 个文件，去除了重复的 `whisper_utils.py` 条目。

### 14.6 🟢 两套 SRT 数据模型 — 设计如此

`srt_utils.py`（行列表）和 `whisper_utils.py`（cue 字典）两层抽象是合理分层，
通过 `whisper_utils.parse_srt()` 桥接。不视为债务。
