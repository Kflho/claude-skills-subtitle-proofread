# 2026 模型资产与调用手册

> **新会话先读这一页**：模型已经下到本机 `models/`（gitignored，不会随仓库提交）。
> 不要重新下载；先按本页路径核对，再调用。最后更新：2026-10-19。

## 0. 速查表

| 模型 | 本机路径 | 大小 | 运行时 | 状态 |
|---|---|---|---|---|
| FireRedVAD / AED | `models/FireRedVAD/{VAD,Stream-VAD,AED}` | ~7 MB | `.venv-qwen` + `fireredvad` 0.0.2（CPU） | ✅ 已下载、已实测 |
| FireRedVAD 代码 | `models/FireRedVAD-code/` | ~1 MB | 参考代码/示例 | ✅ |
| VibeVoice（已删除） | — | — | — | ⛔ 说话人无用，2026-10 用户决定删除；资产已清理 |
| Mel-Band RoFormer | `models/MelBandRoformer/` | fp16 456 MB / fp32 913 MB | 暂无（ComfyUI 格式，需适配） | ✅ 权重已下；运行时未接线 |
| Qwen3-ASR-1.7B | `models/Qwen3-ASR-1.7B` | ~3.9 GB | `.venv-qwen` + `qwen-asr` worker | ✅ 已有 |
| Qwen3-ForcedAligner-0.6B | `models/Qwen3-ForcedAligner-0.6B` | ~1.2 GB | 同上 | ✅ 已有 |
| Whisper（本机 CUDA） | 本机 whisper.cpp 目录（`WHISPER_CLI`） | 已有 | whisper.cpp CLI | ⛔ 按用户规则不再下载/测试 |

> **Whisper 规则**：只保留本机 whisper.cpp CUDA；不要下载 Whisper 系、faster-whisper、WhisperX。

## 1. 环境

- 主环境：系统 Python 3.12（不装 torch）。
- 模型环境：`.venv-qwen\Scripts\python.exe`（torch 2.6.0+cu124、modelscope 1.40.2、fireredvad 0.0.2、qwen-asr）。
- 调用 FireRedVAD 用 `.venv-qwen`；不要用主环境（没有 torch）。
- 模型目录 `models/` 在 `.gitignore` 中，**不要提交权重**；文档放 `docs/`。

## 2. FireRedVAD / AED（✅ 已下载并实测）

### 2.1 路径

```text
models/FireRedVAD/VAD/model.pth.tar
models/FireRedVAD/VAD/cmvn.ark
models/FireRedVAD/Stream-VAD/...
models/FireRedVAD/AED/model.pth.tar
models/FireRedVAD/AED/cmvn.ark
models/FireRedVAD-code/            # 官方 repo（示例、CLI、requirements）
```

权重来自 ModelScope `xukaituo/FireRedVAD` / HF `FireRedTeam/FireRedVAD`（Apache-2.0，0.6M 参数）。
`config.yaml` 是空文件，使用 `FireRedVadConfig` / `FireRedAedConfig` 默认参数。

### 2.2 Python 调用（已验证）

```python
# .venv-qwen\Scripts\python.exe
from fireredvad import FireRedVad, FireRedVadConfig
from fireredvad import FireRedAed, FireRedAedConfig

base = 'models/FireRedVAD'

vad = FireRedVad.from_pretrained(base + '/VAD', FireRedVadConfig(use_gpu=False))
result, probs = vad.detect('path/to/16k_mono.wav')
# result = {'dur': 2.32, 'timestamps': [(0.44, 1.82)], 'wav_path': ...}

aed = FireRedAed.from_pretrained(base + '/AED', FireRedAedConfig(use_gpu=False))
result, probs = aed.detect('path/to/16k_mono.wav')
# result['event2timestamps'] = {'speech': [...], 'singing': [...], 'music': [...]}
# result['event2ratio'] = {'speech': 0.414, 'singing': 0.078, 'music': 0.768}
```

输入必须 **16 kHz 16-bit mono PCM wav**；长音频会自动按 30000 帧切分（日志
`Too long input, split every 30000 frames` 是正常提示）。

### 2.3 本机验证结果

- 官方 `hello_zh.wav`：VAD `(0.44, 1.82)`，与 README 一致；AED ratios 0.848/0.905/0.991。
- EP001 主故事窗口 `[95,1240]`（1145 s）：
  - VAD speech **666.7 s / 290 段**；
  - AED speech 558.8 s / 309 段，singing 101.8 s / 55 段，music 893.8 s / 78 段；
  - 结论：音乐占比 77%（BGM 很重），唱歌 8.9%（OP/ED/插曲候选），可直接做 OP/ED 候选与空洞诊断提示。
- 复测命令（在仓库根）：

```powershell
.venv-qwen\Scripts\python.exe -c "from fireredvad import FireRedVad, FireRedVadConfig; \
v=FireRedVad.from_pretrained('models/FireRedVAD/VAD', FireRedVadConfig(use_gpu=False)); \
print(v.detect('temp/firered_ep001.wav')[0])"
```

### 2.4 已接线：AudioHints provider + CLI

- `lib/audio/base.py`：`AudioEvent` / `AudioHints`（JSON 契约，依赖零）。
- `lib/audio/fireredvad.py`：provider，主环境通过 subprocess 调 `.venv-qwen` 的 worker，
  不在主环境 import torch。
- `scripts/audio/firered_worker.py`：在 `.venv-qwen` 里加载 VAD/AED，输出契约 JSON。
- `scripts/audio/detect_events.py`：CLI，输入 16k mono wav，写 `<EP>.audio_hints.json`。

```powershell
python scripts/audio/detect_events.py --wav temp/firered_ep001.wav `
  --out temp/scans/EP001.audio_hints.json --mode aed
```

已生成 EP001 hints：442 个事件，`speech 48.8% / singing 8.9% / music 78.1%`
（AED 的 speech 与 VAD 的 666.7s 不同，前者更严格；两条都保留作证据）。
下一步：主流程消费 hints（OP/ED 候选、FA 空洞诊断），**只作提示，不参与门控**。

## 3. 说话人 / VibeVoice（已删除）

2026-10 用户决定：**说话人没有用途，删除 VibeVoice 与 diarization 计划**。
`models/VibeVoice-ASR-BitNet` 与 `models/VibeASR.cpp` 已删除。
删除前的能力测试（BitNet 60s：无 who/when、只有纯文本、专名更差）保留在
`docs/capability-tests.md`，防止以后又把它当“说话人方案”捡回来。


## 4. Mel-Band RoFormer（✅ 权重已下载）

### 4.1 路径

```text
models/MelBandRoformer/MelBandRoformer_fp16.safetensors   # 456 MB
models/MelBandRoformer/MelBandRoformer_fp32.safetensors   # 913 MB（下载中）
```

来源：ModelScope `Kijai/MelBandRoformer_comfy`（HF 同名）。

### 4.2 现状与限制

- 这是 **ComfyUI 格式**的 Mel-Band RoFormer 权重；不是原始训练仓库的直接 checkpoint。
- 本仓库还没有分离运行时/适配器；**不要假设直接 `from_pretrained` 可用**。
- 用途：等主链路稳定后，做“原音频 vs 分离音频”的 ASR A/B；默认关，不投入。
- 若要用，优先找 `KimberleyJSN/melbandroformer` 的官方推理代码，或 `audio-separator` 的模型适配；
  确认权重 key 对齐后再接线。

## 5. 集成待办（按顺序）

1. **FireRedVAD → AudioHints**：新建 `lib/audio/base.py`（`AudioEvent`, `AudioHints`, provider 协议）
   + `lib/audio/fireredvad_provider.py`（subprocess 调 `.venv-qwen`，避免主环境 torch）；
   先输出 `speech/music/singing` 时间线，接 OP/ED 候选与空洞诊断。
2. **说话人**：已删除（用户决定）；不要再引入 VibeVoice 或 diarizer。
3. **Mel-Band A/B**：找运行时/适配器；只做分离 vs 原音频的 ASR 对比，默认关。
4. 所有新模型只通过 ADR-011 的接缝接入；没有指标不算完成。

## 6. 下载复现命令（不要重复下）

```powershell
# FireRedVAD（已完成）
hf download FireRedTeam/FireRedVAD --local-dir models\FireRedVAD

# Mel-Band（ModelScope）
.venv-qwen\Scripts\python.exe -c "from modelscope import snapshot_download; \
snapshot_download('Kijai/MelBandRoformer_comfy', local_dir='models/MelBandRoformer', \
allow_file_pattern=['MelBandRoformer_fp16.safetensors','MelBandRoformer_fp32.safetensors'])"
```

HF CDN 在本机很慢（~0.1–0.7 MB/s，常卡住）；ModelScope 可到 2–8 MB/s。
下载中断后用同一命令重跑即可续传（先删 `.incomplete`/`.lock` 残留）。

## 7. 排障

| 症状 | 处理 |
|---|---|
| `ModuleNotFoundError: fireredvad` | 用 `.venv-qwen\Scripts\python.exe`，不要用主环境 |
| FireRedVAD `Too long input` | 正常提示，自动按 30000 帧切分 |
| `config.yaml` 为空 | 正常；用 Config 默认值 |
| HF 下载卡 0 B | 换 ModelScope；或删 `.cache/huggingface/download/*.incomplete` 后重跑 |
| VibeASR 构建报 MSVC | 必须 MinGW GCC/Clang；`cmake` 需自行安装 |
| 误下 Whisper 系 | 不要；本机只保留 whisper.cpp CUDA |

## 8. 新会话检查清单

1. 先读本页，确认 `models/` 路径存在；**不要重新下载**。
2. FireRedVAD 用 `.venv-qwen`，跑一次 `hello_zh.wav` 验证 import/权重。
3. 说话人/VibeVoice 已删除；不要再下载。
4. 所有模型只通过 `docs/adr/011-technology-integration-map.md` 的接缝接入。
5. Whisper 系不下载、不测试。
