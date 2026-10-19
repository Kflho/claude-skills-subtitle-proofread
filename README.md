# Subtitle Proofread

字幕校对 + 日语→中文翻译 skill。面向**已有日文字幕**的动画/剧集项目：
校对乱码/时间轴、统一专名、LLM 翻译、QA/repair。

> 当前边界：不是“只有视频一键出中文字幕”的成品工具；输入需要日文 SRT/ASS。
> OP/ED 歌词翻译尚未接入。FireRedVAD 仅诊断/预研。

## 快速上手

```bash
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
  --asr-python ".venv-qwen/Scripts/python.exe" \
  --translate
```

输出：

```text
translated/zh/<EP>.srt
translated/zh/<EP>.qa.json
```

详细步骤：[references/quickstart.md](references/quickstart.md) ·
环境配置：[references/setup.md](references/setup.md) ·
翻译参数：[references/translation.md](references/translation.md)。

## 能力

- 日语 SRT/ASS 扫描、乱码检测、时间轴修复；
- Qwen3-ASR + ForcedAligner（60s 切块）或 legacy Whisper；
- LLM 翻译：批处理、按批 glossary、ID 协议、QA、问题条 repair；
- 专名提取/统一、系列词表验证；
- 输出中文 SRT + QA 报告，不覆盖源字幕。

## 环境

- Python 3.12+、ffmpeg；
- LLM：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；
- Qwen3 路线：`.venv-qwen` + `models/Qwen3-ASR-1.7B` + `models/Qwen3-ForcedAligner-0.6B`；
- 本机绝对路径/配置不写入仓库，由维护者本地保存。

## 已知限制

- 只处理已有源字幕；视频直出尚未接入；
- OP/ED 翻译未完成；
- FireRedVAD 当前只写诊断 hints，没有决策消费者；
- 整集翻译后请先看 QA，残留/术语问题按报告修。

## License

MIT
