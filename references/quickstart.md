# 快速上手（当前主路径）

> 目标：已有日文字幕 → 中文 SRT；或已有日文字幕 + 视频 → 修时间轴后翻译。
> 首次环境配置见 [setup.md](setup.md)。

## 1. 只翻译已有日文字幕（最快）

```bash
# 单文件
python scripts/translate_srt.py \
  --input EP001.srt --output 中文/EP001.srt \
  --mappings glossaries/digimon.series.json \
  --qa-report 中文/EP001.qa.json \
  --repair

# 目录
python scripts/translate_srt.py \
  --input-dir 日文/ --output-dir 中文/ \
  --mappings glossaries/digimon.series.json \
  --repair
```

要点：

- 没有 `--mappings` 也能翻，但专名会漂；有系列词表就传。
- `--qa-report` 输出覆盖率/日语残留/回显/术语/长度/CPS/时间轴检查。
- `--repair` 只修 QA 标出的条；建议保留，失败不会阻断。
- 默认 10 条一批；不要改成整集一批（实测更差）。

## 2. 整条 pipeline（修复 + 翻译）

```bash
python scripts/run_all.py \
  --input-dir "<字幕目录>" \
  --video-dir "<视频目录>" \
  --lang ja \
  --asr qwen3 \
  --asr-python ".venv-qwen/Scripts/python.exe" \
  --translate
```

- `--asr qwen3`：Qwen3-ASR + ForcedAligner，只改时间码，不删条。
- 不加 `--asr` 则走 legacy Whisper 路线。
- `--translate`：输出 `translated/zh/<EP>.srt` + `<EP>.qa.json`，不覆盖源字幕。
- `--audio-hints`：FireRedVAD 诊断 JSON，当前**没有决策消费者**，默认不要依赖。

## 3. 看结果

```text
translated/zh/<EP>.srt      # 中文
translated/zh/<EP>.qa.json  # QA
```

QA 里优先看：

| 字段 | 含义 |
|---|---|
| `coverage` | 输出条数/源条数 |
| `japanese_residual` | 残留假名 |
| `echo` | 原文回显 |
| `glossary_miss` | 术语未按词表 |
| `max_chars` / `max_cps` | 行长/阅读速度 |
| `timing` | 时间轴漂移（应为 0） |

## 4. 已知边界

- 输入是**已有日文字幕**；只有视频、没有源字幕时，当前不是一键出中文。
- 主窗口 `EPxxx.main.srt` 不含 OP/ED；OP/ED 翻译尚未接入。
- FireRedVAD 目前只做诊断/预研；OP/ED 消费者未完成。
- 长集异常先看 QA，不要直接全量重翻。

## 5. 失败时

- 缺 API key：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。
- 缺 Qwen 环境：确认 `.venv-qwen` 和 `--asr-python`。
- 路径报错：`--target-dir` 用绝对路径。
- 更多命令：[translation.md](translation.md)、[full-mode.md](full-mode.md)。
