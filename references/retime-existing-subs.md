# 文字正确、时间轴错位：重新打轴

## 适用

- 字幕文本抽查与台词一致，只是整体偏移、逐渐漂移、cue 边界切早/切晚；
- 已有字幕从别的片源/帧率/版本搬来，文字对、轴不对；
- 英文/日文字幕需要重新对到当前视频。

不适用：文本本身有错听、漏句、翻译错误。那要先修文本，再谈轴。

## 原则

1. **文本不动**：`asr/align_cues.py` 默认 time-only，`--write` 只写起止时间；
2. **先诊断后写盘**：不加 `--write` 先看 `summary`、`plan`、`alignment.json`；
3. **不要用 `--fix-text`**：它只用于乱码/错词；本场景加了会改字幕文字；
4. **一次只改轴**：先交付时间轴版本，再做翻译/润色，避免两个变量一起动。

## 文本语言与音频一致

```bash
# 1) 只诊断
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --video "<video>" \
  --language English --engine qwen3 \
  --python "<qwen-venv>/Scripts/python.exe"

# 2) 已有 ASR JSON 时跳过模型
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --asr-json "temp/asr/<EP>.json"

# 3) 确认后写回时间码
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --asr-json "temp/asr/<EP>.json" --write
```

看报告里的 `summary`：匹配率、时间修复条数、需要人工复核的条数。`plan` 里
`auto=true` 的是安全时间修复；`review` 的逐条看，不要一把梭。

## 文本语言与音频不一致

英文/中文字幕配日语音轨时，cue 文本和 ASR 文本对不上，直接跑会把匹配率拉低，
甚至给出错误时间。按顺序选：

1. 优先找**源语言字幕**（日语原字幕）先对齐，再按句序把译文时间映射过去；
2. 没有源字幕：先转录源语言，按句序/参考字幕建立对应，再套译文；
3. 有同语言配音（例如英语配音）时，用该音轨的 ASR 对齐英文字幕；
4. 都做不到：只做整段平移/等间隔插值，并明确标为低置信度，不要伪装成逐句对齐。

## 参数

| 参数 | 用途 |
|---|---|
| `--srt` | 待重打轴字幕 |
| `--asr-json` | 已有 ASR 结果，省模型时间 |
| `--audio` / `--video` | 16k WAV 或视频；二选一 |
| `--language` | ASR 语言，英文用 `English` |
| `--engine` | 默认 `qwen3`；有 Whisper 环境可用 `whisper` |
| `--write` | 写回时间码；不加只诊断 |
| `--fix-text` | **本场景不要用** |
| `--search-seconds` | 每 cue 在 ASR 里搜索的窗口 |
| `--max-shift` | 允许的最大单条位移，超出转人工 |
| `--timeline-source` | 时间轴来源策略，默认 auto |
| `--repair-overlong` | 修复超长 cue，按需 |

## 验收

- 抽 5–10 个密集对话点，看口型/停顿是否对上；
- 检查 `alignment.json` 的 review 条数，未处理完不算完成；
- 跑 `qa.py` 或 `run_all.py` 的 QA 步骤，确认无负时长/重叠/越界；
- 文本逐字对比原 SRT，确认没有被 `--fix-text` 改动；
- 保留 `.bak` 或版本控制，写回前可回滚。
