# Subtitle Proofread

字幕校对 + 日语→中文翻译 skill。支持：
**已有日文字幕**校对/翻译，或**只有视频**的一键粗剪（Qwen3 转写 → LLM 翻译 → QA）。

> 当前边界：视频一键出的是句子级粗剪，不是最终字幕。
> OP/ED 走「音频指纹定窗口 + 画面复核 + 定本歌词替换」（见 `audio/oped_boundaries.py`、`fix/oped_region.py`）。
> FireRedVAD 仅诊断/预研。

## 快速上手

```bash
# 只有视频 → 日文 SRT → 中文 SRT
python scripts/video_to_zh.py \
  --video-dir "<视频目录>" --output-dir "<输出目录>" \
  --engine qwen3 --mappings glossaries/digimon.series.json

# 只翻译已有日文字幕
python scripts/translate_srt.py \
  --input EP001.srt --output 中文/EP001.srt \
  --mappings glossaries/digimon.series.json \
  --qa-report 中文/EP001.qa.json \
  --repair

# 校对 + 时间轴 + 翻译（需要视频与 Qwen3 环境）
python scripts/run_all.py \
  --input-dir "<字幕目录>" \
  --video-dir "<视频目录>" \
  --lang ja \
  --asr qwen3 \
  --asr-python "<qwen-python>" \
  --translate
```

输出：

```text
translated/zh/<EP>.srt
translated/zh/<EP>.qa.json
# 视频直出：<output>/ja/<EP>.srt + <output>/zh/<EP>.srt + qa.json
```

> `video_to_zh.py` 是 **A 转写 + C 翻译** 的快捷粗剪，不替代原有完整 A–D：
> 专名审查、`auto_translate`、`oped_fixer`、`polish_zh` 见
> [references/workflows.md §5](references/workflows.md#场景-5完整流程视频--成品中文)。
> 质量证据：旧 Whisper 文本更好（CER 0.204 vs Qwen3 0.263），Qwen3 时间轴更好
> （P50 400 vs 757ms）；**没有端到端中文质量 A/B**，不能声称新路线整体更好。

详细步骤：[references/quickstart.md](references/quickstart.md) ·
环境配置：[references/setup.md](references/setup.md) ·
翻译参数：[references/translation.md](references/translation.md)。

## 能力

- 日语 SRT/ASS 扫描、乱码检测、时间轴修复；
- Qwen3-ASR + ForcedAligner（worker 自切块，默认 60s；老音源用 `--chunk-sec 20 --max-batch 8`）
  或 legacy Whisper；整季一条命令 `video_to_zh.py`（已有 `ja/<EP>.srt` 自动 reuse，透传上面两个参数）；
- **OP/ED 时间段定位**：`audio/oped_boundaries.py` 跨集音频指纹互相关定窗口（`--frames-dir` 抽边界帧做画面复核），
  不依赖 ASR 唱词，也不吃固定窗口；
- **OP/ED 唱词定稿**：`fix/oped_region.py` 按窗口把该段 cue 换成定本歌词（按时长/字数分配），
  配合 `fix/oped_fixer.py`（跨集变体统一）与 `nouns/context_review.py --mode oped`（`_meta.fixed_lines` 优先）；
- LLM 翻译：批处理、按批 glossary、ID 协议、QA、问题条 repair；
- 专名提取/统一、系列词表验证、**变体归一**（分批翻译漂移用确定性替换收敛）；
- 译后**质量闸门**：kana 残留 / 回显 / 变体 / CPS 四项计数，超阈值退出 1；
- **能力清单**：`capabilities.json` 按常驻 / 阶段 / 交付前三层登记入口与触发词，
  `python scripts/capabilities.py --report` 看矩阵、`--check` 抓失效项；
- 输出中文 SRT + QA 报告，不覆盖源字幕。

## 环境

- Python 3.12+、ffmpeg；
- LLM：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；
- Qwen3 路线：`<qwen-python>` + `<models-dir>/Qwen3-ASR-1.7B` + `<models-dir>/Qwen3-ForcedAligner-0.6B`；
- 本机路径/密钥不写入仓库：同步后在 harness 副本运行 `scripts/harness/init_local.py --force`，写入 `local.json`。

## 已知限制

- 视频直出已接，但 ASR 是句子级、raw text 有错，输出是粗剪；
- OP/ED **唱词**走「音频指纹定窗口 + 定本歌词替换」，译文是人工定稿，不参与 ASR/翻译质量证据；
  定本行数与实际演唱句数要人工核对一遍；
- 唱歌区间的 ASR 文本仍会乱写，替换前别拿它判断歌词；
- FireRedVAD 当前只写诊断 hints，没有决策消费者；
- 整集翻译后请先看 QA，残留/术语问题按报告修。

## License

MIT
