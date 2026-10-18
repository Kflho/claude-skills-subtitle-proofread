# Qwen3-ASR + P2 对齐 运行说明

> 2026 技术路线的可运行切片：P1 **转录 + 评测**，P2 **cue 对齐 + 时间码修复**。
> 两者都不改默认管线；`run_all.py` 仍走 Whisper。

## 1. 安装（隔离 venv）

```powershell
# 在项目根目录
powershell -ExecutionPolicy Bypass -File scripts\asr\setup_qwen3.ps1
```

国内网络如果 PyPI 超时，加镜像：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\asr\setup_qwen3.ps1 `
  -IndexUrl https://pypi.tuna.tsinghua.edu.cn/simple
```

脚本会：

1. 创建 `.venv-qwen`（`--system-site-packages`，复用主环境的 torch）；
2. 安装 `qwen-asr==0.0.6`（固定 `transformers==4.57.6`）；
3. 跑 `--check`，报告 torch / CUDA / qwen_asr 是否就绪。

模型不会自动下载（约 5GB）。需要时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\asr\setup_qwen3.ps1 -SkipInstall -DownloadModels
```

或手动：

```powershell
.\.venv-qwen\Scripts\python.exe -m huggingface_hub.commands.huggingface_cli download Qwen/Qwen3-ASR-1.7B --local-dir models\Qwen3-ASR-1.7B
.\.venv-qwen\Scripts\python.exe -m huggingface_hub.commands.huggingface_cli download Qwen/Qwen3-ForcedAligner-0.6B --local-dir models\Qwen3-ForcedAligner-0.6B
```

> 国内网络可用 ModelScope：
> `modelscope download --model Qwen/Qwen3-ASR-1.7B --local_dir models\Qwen3-ASR-1.7B`

## 2. 检查

```powershell
.\.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py --check
```

期望看到 `torch ... cuda=True`、`qwen_asr: ...`、`OK`。

## 3. 单文件转录

```powershell
# 在隔离 venv 里
.\.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio ep.wav --language Japanese --timestamps `
  --context-file glossary.txt --output temp\asr\EP001.json --srt temp\asr\EP001.srt

# 从主环境调用（推荐：主环境不装 qwen-asr）
python scripts\asr\qwen3_transcribe.py `
  --audio ep.wav --python .venv-qwen\Scripts\python.exe `
  --output temp\asr\EP001.json
```

视频也可以直接给（ffmpeg 抽 16k WAV）：

```powershell
python scripts\asr\qwen3_transcribe.py --video ep.mkv `
  --python .venv-qwen\Scripts\python.exe --srt temp\asr\EP001.srt
```

## 4. 与 Whisper A/B

```powershell
# 旧管线（需要 WHISPER_MODEL / WHISPER_CLI）
python scripts\eval\asr_eval.py --manifest eval\manifest.json `
  --engine whisper --output reports\asr_eval_whisper.md

# 新管线
python scripts\eval\asr_eval.py --manifest eval\manifest.json `
  --engine qwen3 --python .venv-qwen\Scripts\python.exe `
  --output reports\asr_eval_qwen3.md
```

manifest 格式和指标说明见 [`eval/README.md`](../../eval/README.md)。

## 5. cue 对齐（P2）

```powershell
# 1) 先拿 ASR JSON（可复用第 3 步）
python scripts\asr\align_cues.py --srt ep.srt --asr-json temp\asr\EP001.json

# 2) 直接给视频（内部先转录再对齐）
python scripts\asr\align_cues.py --srt ep.srt --video ep.mkv --engine qwen3 `
  --python .venv-qwen\Scripts\python.exe

# 3) 确认无误后再落盘（默认 dry-run）
python scripts\asr\align_cues.py --srt ep.srt --asr-json temp\asr\EP001.json --write
```

输出：

- 终端摘要：`match/garbled/missing_asr/extra_text/no_speech` 计数、动作计数、边界 MAE/P50/P90/±100ms；
- JSON（默认 `temp/align/<stem>.alignment.json`）：每 cue 的 `status/similarity/asr_span/start/end/signals`
  以及修复计划 `repairs`，可复算、可 A/B。

安全默认：

| 开关 | 默认 | 说明 |
|---|---|---|
| `--write` | 关 | 只出报告，不改字幕 |
| `--fix-text` | 关 | 文本替换需要显式开启（且只在 garbled 且可判定时） |
| `--max-shift` | 2.0s | 超过的时间修正转 review，不自动写 |
| 删除 cue | 永不 | `no_speech/extra_text` 只进审查清单 |
| ASS + `--write` | 拒绝 | ASS 写回目前只改文本、不改时间码，避免静默无操作 |

状态与置信度规则见 [`docs/adr/007-cue-alignment.md`](../../docs/adr/007-cue-alignment.md)。

## 6. Worker 协议

主进程不会 import torch；它通过 `scripts/asr/qwen3_worker.py` 的 JSON lines 通信：

```json
{"cmd":"ping"}
{"cmd":"load"}
{"cmd":"transcribe","id":"EP001","audio":"C:/tmp/EP001.wav","language":"Japanese","context":"...","timestamps":true}
{"cmd":"align","id":"cue-1","audio":"C:/tmp/scene.wav","text":"お前、何してんだよ！","language":"Japanese"}
{"cmd":"shutdown"}
```

响应统一为 `{"ok": true, "id": ..., "result": {...}}` 或 `{"ok": false, "error": ...}`。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `qwen_asr: MISSING` | 用 `.venv-qwen\Scripts\python.exe` 跑 `--check`，或在主环境 `pip install qwen-asr` |
| `CUDA requested but torch.cuda.is_available() is False` | 检查 CUDA 版 torch；或 `--device cpu`（慢） |
| `model not found` / 下载失败 | 用 ModelScope 下载，或把 `ASR_MODEL` / `ALIGNER_MODEL` 指向本地目录 |
| `transformers` 版本冲突 | 不要在主管线环境装 qwen-asr；用隔离 venv |
| 3 分钟边界抖动 | P1 只输出时间戳；P2 的 cue mapper 会处理边界连续性 |
| 显存不足 | 用 0.6B ASR（`--asr Qwen/Qwen3-ASR-0.6B`），或先跑 3 分钟片段 |

## 8. 当前边界

- **P1 不修改 SRT**：转录只写 JSON / 可选 SRT 参考。
- **P2 默认 dry-run**：`align_cues.py` 需要 `--write` 才改时间码，`--fix-text` 才换文本。
- **不接 `Fixer`**：`run_all.py` 仍走旧 Whisper；`fix_by_asr()` 是 P2 尾项。
- **时间戳依赖 aligner**：`--no-timestamps` 时只返回文本/segment，`tokens=[]`。
- **Qwen3 不返回置信度**：P2 用对齐一致度 + 多信号投票重建。

## 9. 新增模型（不改 core）

1. 复制 `templates/asr_adapter.py` 到 `scripts/lib/asr/adapters/<name>.py`；
2. 填 `NAME/KIND/INFO/build`，实现 `available()/transcribe()`；
3. 加契约测试（`validate_backend` + fake 调用，不下载模型）；
4. 跑 `python scripts/asr/qwen3_transcribe.py --list-engines`，应自动出现；
5. 用 `--engine <name>` 直接跑，不需要改 router/CLI/评测。

对齐器同理：复制 `templates/aligner_adapter.py`，`KIND='aligner'`，实现 `align()`。
详见 [`docs/adr/006-stable-asr-core.md`](../../docs/adr/006-stable-asr-core.md)。
