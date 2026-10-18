# P0 ASR 评测清单

> 目标：同一批 12 集，分别跑旧管线（Whisper）与新管线（Qwen3-ASR + FA），比较 CER、边界误差、人工审查条数、耗时。

## 0. 当前登记素材（2026-10-18）

- 作品：**数码宝贝（Digimon Adventure）**
- 目录：`D:\videos\Animation\TV\数码宝贝`
- 现状：EP01 视频已完整（用户人工核对日文 CC 与视频同步）；EP02/03 `.mkv.downloading` 中；**日文 CC 已有 S01E01–S01E54（54 集）**；01–05 另有中文字幕（`.sc` / `.tc` ASS）。
- 日文 CC 是配信闭合字幕，含说话人标签/振假名/♪，**直接算 CER 前需清洁**（`scripts/eval/clean_cc_reference.py`）；明细与规则见 [`materials.md`](materials.md)。
- 评测清单草稿见 [`manifest.digimon.json`](manifest.digimon.json)。

> 当前等 EP02/03 视频下载；日文字幕已够。中文字幕不能当日文 CER 金标。

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

## 4. 看什么

- `CER`：字符错误率，主指标；相对下降 ≥15% 才算达标；
- `boundary MAE / P90`：ASR segment 与参考 cue 的边界差，P0 只作诊断；
- `RTF`：单集耗时 / 音频时长；
- 错误列表：哪几集失败、为什么。

> 注意：P0 的 `boundary MAE` 是「segment ↔ cue 最大重叠匹配」，不是真正的 cue 对齐。P2 的 `cue_mapper` 上线后替换为 token 级指标。

## 5. 记录

把结果填进 `dev/tech-route-progress.md` 的「P0 基线」一节，并保留 JSON/MD 产物。
