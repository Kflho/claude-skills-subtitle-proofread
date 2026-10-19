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
- 默认不塞前文、不预替换专名；`--context-window` / `--noun-pre-replace` 是实验开关。
  现有 EP001–003 实验里默认 3 条上下文没有稳定收益，固默认 0。

## 2. 只有视频 → 中文（一键，粗剪）

```bash
python scripts/video_to_zh.py \
  --video-dir "<视频目录>" \
  --output-dir "<输出目录>" \
  --engine qwen3 \
  --mappings glossaries/digimon.series.json
```

输出：

```text
<输出目录>/ja/<EP>.srt      # Qwen3 转写
<输出目录>/ja/<EP>.asr.json
<输出目录>/zh/<EP>.srt      # 中文
<输出目录>/zh/<EP>.qa.json
```

说明：

- 内部只串现有工具：`qwen3_transcribe.py`（或 `--engine whisper`）→ `translate_srt.py`；
- Qwen3 转写是句子级，**断句/分行不等于最终字幕**；先看 QA，再做 cue 级人工/后续处理；
- raw ASR 文本可能有错（`水田→水電`），翻译会继承；这是视频直出的质量上限，不是模型不会；
- OP/ED 仍未覆盖。

> **这不是原有完整流程的替代品。** `video_to_zh.py` 只做 A（转写）+ C（翻译）的快捷粗剪；
> 原有的 B（专名审查）、D（`auto_translate` / `oped_fixer` / `polish_zh`）仍在
> [workflows.md §5](workflows.md#场景-5完整流程视频--成品中文)。要交付成品，走那条 A–D。

### 视频直出选哪条？

| 路线 | 文本 | 时间轴 | 现状 |
|---|---|---|---|
| Qwen3-ASR + FA | 略差 | 更好 | `--engine qwen3`，wrapper 默认 |
| Whisper legacy | 更好 | 更差（segment 级） | `--engine whisper`，需要 `WHISPER_CLI`/`WHISPER_MODEL` |

已有 3 集 A/B（同一参考/窗口）：Whisper test CER **0.204**，Qwen3 **0.263**；
但 Qwen3 test P50 **400ms** vs Whisper **757ms**，可靠 P90 **510ms** vs **1325ms**。
所以：

- **文本质量**：旧 Whisper 更好；
- **时间轴**：Qwen3 + FA 更好；
- **没有端到端中文质量 A/B**：不能声称新路线整条字幕更好。

只想“先出能看的中文粗剪”可用 Qwen3；更在意识别文本可走 Whisper 旧路径。

> 2026-10 复核：3×60s 片段里 Qwen3 的日文 CER 反而更低（.110/.131/.115 vs
> Whisper .118/.178/.153），但中文 chrF/QA Whisper 略好或持平；Qwen3 出现较长 cue
> 和行长违规。**样本小，未做整集 A/B，不能据此切换默认。**

## 3. 整条 pipeline（修复 + 翻译）

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

## 4. 看结果

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

## 5. 已知边界

- **视频→中文已接**，但 Qwen3 转写是句子级、raw ASR 有错；输出是粗剪，需看 QA/后续断句。
- 主窗口 `EPxxx.main.srt` 不含 OP/ED；OP/ED 翻译尚未接入。
- FireRedVAD 目前只做诊断/预研；OP/ED 消费者未完成。
- 长集异常先看 QA，不要直接全量重翻。

## 6. 失败时

- 缺 API key：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。
- 缺 Qwen 环境：确认 `.venv-qwen` 和 `--asr-python`。
- 路径报错：`--target-dir` 用绝对路径。
- 更多命令：[translation.md](translation.md)、[full-mode.md](full-mode.md)。
