# P0 ASR 评测清单

> 目标：同一批 12 集，分别跑旧管线（Whisper）与新管线（Qwen3-ASR + FA），比较 CER、边界误差、人工审查条数、耗时。

## 0. 当前登记素材（2026-10-18，三集就绪）

- 作品：**数码宝贝（Digimon Adventure）**
- 目录：`D:\videos\Animation\TV\数码宝贝`
- 现状：**EP01/02/03 视频已完整**；日文 CC 已有 S01E01–S01E54（54 集）；01–05 另有中文字幕（`.sc` / `.tc` ASS）。
- 日文 CC 是配信闭合字幕，含说话人标签/振假名/♪，**直接算 CER 前需清洁**（`scripts/eval/clean_cc_reference.py`）；明细与规则见 [`materials.md`](materials.md)。
- 评测清单见 [`manifest.digimon.json`](manifest.digimon.json)：EP01=dev，EP02/03=test。
- **参考字幕是考卷，不是教材**：只用于打分，不能用来反推阈值/prompt。协议见 [`protocol.md`](protocol.md)。

> 中文字幕不能当日文 CER 金标；日文 CC 清洁后仍含字幕卡/歌词，CER 只作相对比较。

## 1. 准备素材

每集需要：

- 视频或 16k mono WAV；
- **可信的日文参考字幕**：人工听校/人工翻译的 SRT，或明确可追溯到人工来源的字幕；
- 类别：`clean` / `overlap` / `bgm`。

> ⚠️ **金标来源规则**：机器翻译、ASR 输出（包括 Whisper 转录）、LLM 润色后的字幕**不能**当参考字幕。
> 用它们算 CER / 边界误差只会测到「两个模型像不像」，不是「谁更对」。
> 没有人工参考时，只允许做参考无关指标（跨引擎一致度、时间轴覆盖、RTF、幻觉/重复度），并明确标注「非金标」。
> **验收素材由用户提供（完整字幕 + 视频）；莎莉 / 阿童木等旧素材字幕质量差，不得作为参考。**

建议每类 4 集，共 12 集。没有 12 集先跑 3 集（每类 1 集）。

## 2. 填 manifest

复制 `eval/manifest.example.json` 为 `eval/manifest.json`，把路径换成自己的：

```json
{
  "project": "astro-boy",
  "language": "Japanese",
  "episodes": [
    {"id": "EP001", "category": "clean", "audio": "...", "reference_srt": "..."}
  ]
}
```

`audio` 和 `video` 二选一；给 `video` 时评测脚本会用 ffmpeg 抽 16k WAV。

## 3. 跑基线

```powershell
# 旧管线：Whisper
python scripts/eval/asr_eval.py --manifest eval/manifest.json `
  --engine whisper --json-out reports/asr_eval_whisper.json `
  --output reports/asr_eval_whisper.md

# 新管线：Qwen3（模型已装在 .venv-qwen）
python scripts/eval/asr_eval.py --manifest eval/manifest.json `
  --engine qwen3 --python .venv-qwen\Scripts\python.exe `
  --json-out reports/asr_eval_qwen3.json `
  --output reports/asr_eval_qwen3.md
```

### 3.1 三集 held-out 基准（推荐）

主故事窗口、CER、token 时间轴质量、cue 对齐一次出齐；只读，不生成阈值：

```powershell
# Qwen3 主口径（raw / 无偏移；参考字幕只打分）
python scripts/eval/episode_benchmark.py --manifest eval/manifest.digimon.json `
  --json-out temp/reports/digimon_qwen3_raw.json `
  --output temp/reports/digimon_qwen3_raw.md

# Whisper large-v3 q5 A/B（同参考、同窗口）
python scripts/eval/episode_benchmark.py --manifest eval/manifest.digimon.whisper.json `
  --json-out temp/reports/digimon_whisper_raw.json `
  --output temp/reports/digimon_whisper_raw.md

# 可选：只对确有独立证据的整文件漂移做全局偏移（不要用它吸收 ASR/CC onset bias）
... --global-offset
```

> 2026-10-18 用户人工复核：视频/CC 无真实偏移；`--global-offset` 不是默认项，
> offset-corrected 数字不能当验收。

对比时只看同一 split、同一 ASR 缓存；dev 看方向，test 冻结后跑一次。

报告第八轮起还带三块测量有效性信息（读法见 `eval/protocol.md` §2.1）：

- **Signed start error**：P10/P50/P90、early share、P90−P10。test 集 90% 的 matched cue
  起点早 −0.37/−0.40 s，这是 CC 显示起点约定；报告它，但**不要减掉**。
- **Text decomposition**：字符覆盖率、matched-span CER（相似度 ≥0.85 / ≥0.5）、
  无匹配字符数。Qwen3 三集只覆盖 42–45% 的 CC 字符，P90 不能外推整集。
- **Timeline holes**：塌缩 run + 前置空洞（EP001–03：19/7/9 个，累计 229/122/96 s），
  是 P3 VAD 的输入清单，不是 P2 的自动修复对象。


## 4. 看什么

- `CER`：字符错误率，主指标；相对下降 ≥15% 才算达标；
- `boundary MAE / P90`：ASR segment 与参考 cue 的边界差，P0 只作诊断；
- `RTF`：单集耗时 / 音频时长；
- 错误列表：哪几集失败、为什么。

> 注意：P0 的 `boundary MAE` 是「segment ↔ cue 最大重叠匹配」，不是真正的 cue 对齐。P2 的 `cue_mapper` 上线后替换为 token 级指标。

## 5. 记录

把结果填进 `dev/tech-route-progress.md` 的「P0 基线」一节，并保留 JSON/MD 产物。
