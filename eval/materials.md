# 金标素材登记

> **用户指定（2026-10-18）**：验收作品改为 **数码宝贝（Digimon Adventure）**。
> **素材目录**：`D:\videos\Animation\TV\数码宝贝`
> 本文件是素材台账；`eval/manifest.digimon.json` 是评测清单草稿。

## 1. 目录现状

| 集 | 视频 | 中文字幕 | 日文参考字幕 | 备注 |
|---|---|---|---|---|
| 01 漂流？冒険の島！ | ✅ `01、漂流？冒険の島！.mkv` | ✅ `.sc.ass` / `.tc.ass` | ✅ `日语字幕\…S01E01…ja[cc].srt` | 视频+日文 CC 到齐 |
| 02 爆裂進化！グレイモン | ✅ `02、爆裂進化！グレイモン.mkv` | ✅ `.sc.ass` / `.tc.ass` | ✅ `…S01E02…ja[cc].srt` | 视频完整；待人工确认 CC 同步 |
| 03 蒼き狼！ガルルモン | ✅ `03、蒼き狼！ガルルモン.mkv` | ✅ `.sc.ass` / `.tc.ass` | ✅ `…S01E03…ja[cc].srt` | 视频完整；待人工确认 CC 同步 |
| 04 灼熱！バードラモン | ⬜ | ✅ | ✅ `…S01E04…ja[cc].srt` | 缺视频 |
| 05 電光！カブテリモン | ⬜ | ✅ | ✅ `…S01E05…ja[cc].srt` | 缺视频 |
| … | ⬜ | — | ✅ S01E06–S01E54 | 日文字幕共 **54 集** |

**日语字幕目录**：`D:\videos\Animation\TV\数码宝贝\日语字幕`
文件命名：`デジモンアドベンチャー.S01E01.WEBRip.ja[cc].srt` … `S01E54`。

### EP01 技术检查（2026-10-18）

```text
视频：01、漂流？冒険の島！.mkv  1.26 GB
时长：1386.26s（23:06）
流：H264 video (jpn) + FLAC 48 kHz stereo (jpn)
中文字幕：230 cue，01:38.93 → 23:03.58，ASS UTF-16LE，人工字幕组
日文 CC：296 cue，00:00.959 → 22:48.366，UTF-8 BOM SRT
  字符：3273（假名 2113 / 汉字 472 / 拉丁 0）
  注释：97 cue 含「（…）」标签，24 cue 含振假名 `(かな)`，
        4 cue 只有 ♪，6 cue 只有括号标签
```

> **用户人工核对（2026-10-18）**：EP01 日文 CC 与视频**对得上**。
> 因此首轮高 CER 不是版本错配，主要来自 ASR 省略短语气词/字幕卡、写法差异，以及 token 时间轴在 OP/ED 段塌缩。

## 2. 素材判定

- **视频**：EP01 可解析，日语 FLAC 立体声；做 ASR / 对齐 / 翻译冒烟没问题。
- **日文参考（已到位）**：`ja[cc]` 是日文配信 CC（闭合字幕），用户已人工核对 EP01 与视频同步；
  内容为原生日语，含 **旁白/说话人标签、振假名、音效/歌词标记**，以及部分字幕卡/角色名。
  - **不能直接拿去算 CER**：标签/振假名/♪ 会被当成插入错误；字幕卡与短语气词也会影响；
  - 需要先做「CC 清洁」：去 `（…）` 标签、去 `(かな)` 振假名、去纯 ♪/音效行；
  - 主故事窗口约 **95s–1233s**（OP 约 1:39 结束，ED 约 20:39 开始），ED/预告在 20:39–22:48。
- **中文字幕**：`.sc` 简体 / `.tc` 繁体，人工字幕组，但**不是日文参考**；
  只做翻译/复用/参考无关测试，不能当日文 CER 金标。
- **当前状态**：日文字幕已够（54 集，超过 12 集要求）；**缺视频**（只有 EP01）。
  P2-14 验收尚未开始。

## 3. 日文字幕接收/使用约定

- 已放在 `日语字幕\` 子目录，命名 `S01E01`…`S01E54`；继续沿用即可。
- 评测前先清洁 CC（建议输出到 `temp/references/`，保留原文件不动）：
  - 去全角括号标签：`（ナレーション）`、`（八神太一(やがみたいち)）`、`（子どもたち）`；
  - 去半角振假名：`降(ふ)らず` → `降らず`、`洪水(こうずい)` → `洪水`；
  - 去纯音乐/音效行（`♪～`、`（効果音）` 等）；
  - 检查 ED/预告：EP01 参考最后 22:48，视频 23:06；若 CC 未收 ED，比较时需截到参考末尾或排除 ED。
- 清洁后按 `eval/manifest.digimon.json` 填 `reference_srt`，再跑 `asr_eval.py` / `alignment_bench.py`。
- 主故事边界（三集同结构）：OP 到约 1:39；ED 约 20:39；预告到 22:48。评测主对白用 95–1233s。

## 4. 数量要求（复核）

| 用途 | 最低 | 建议 | 当前 |
|---|---:|---:|---|
| 管线冒烟 / RTF / 时间轴 | 1 集 | 1–3 集 | ✅ EP01 |
| 单集 A/B | 3 集 | 5 集 | 🟡 EP02/03 下载中 |
| P2-14 金标验收（CER / 边界 P50/P90） | **3 集（clean/overlap/bgm 各 1）** | **12 集（每类 4）** | 日文✅ 54 集；视频🟡 |
| chunk 调参（45/60/90s） | 3 集 | 12 集 | 🟡 等视频 |

> **当前阻塞：EP02/03 视频还在下载**。用户计划用前三集；日文字幕已有 54 集。

## 5. 素材接收（2026-10-18 22:29）

- 压缩包已下载/解压到 `D:\videos\Animation\TV\数码宝贝`：EP01/02/03 `.mkv` 完整（各 ~1.26–1.37 GB，约 1386 s）。
- 04–05 只有中文字幕，没有视频；日文 CC 共 54 集。
- EP01 用户已人工确认 CC 与视频同步；EP02/03 尚未逐句确认。三集基准实测全局偏移 -0.37 / -0.40 s，可能是音频切点或 CC 版本差异，**不能当成 CC 错**，需人工听校后再定。
- 清单 `eval/manifest.digimon.json`：EP01=dev，EP02/03=test；主故事窗口 `[95,1240]`；ASR 缓存 `temp/asr/digimon_ep00X_main.qwen3.json`。

## 6. EP01 首轮冒烟（2026-10-18，非验收）

素材：EP01 视频 + 清洁后的日文 CC（286 cue）。Qwen3-ASR 1.7B + FA，60s 分块。

```text
转写：2427 chars / 1188 tokens / 253 segments / 1386.3s
耗时：105.2s（eval）/ RTF 0.076
P0 全文件 CER=0.4522（含 OP/ED 歌词与版本差异，不作质量结论）
P0 boundary MAE=4810ms（segment↔cue 最大重叠，长 segment 下无意义）
P2 align：match 55/286，garbled 115，missing_asr 74，no_speech 40
         （已匹配的 55 条：P50 140ms / P90 2380ms / ±100ms 45.5%）
```

诊断（用户已人工确认 CC 与视频对得上）：

- ASR 文本内容与 CC 基本对得上（例：第一段旁白相似度 0.88+），但 **token 时间轴在 OP/音乐段塌缩**：
  95 个零时长 token；全文件旁白 ASR 落在 75.5s，而 CC/画面在 99.5s；全文件 cue 覆盖只有 0.56。
- 只跑正片（95–1240s）后，首段 token 恢复（旁白 0.9s vs 参考 4.47s），但 cue 覆盖仍约 0.50：
  Qwen3 省略短语气词（`あ…`/`うわー`），CC 又含角色名字幕卡，按 cue 算会大量 unmatched。
- ASR 文本包含 OP/ED 歌词与次回预告；CC 主故事到 20:33，ED 在 20:39，预告到 22:48。
- CC 写法（ケータイ/片假名名、语气词）与 ASR（携帯/汉字名、省略语气词）不同，也会抬高 CER。

**结论**：首轮数字不能当 P2-14 结果。三集评测改用 **主故事窗口 [95,1240]**，
参考只作 held-out 基准（`eval/protocol.md`），不回流参数；短语气词/字幕卡单独统计。

### 6.1 三集基准（2026-10-18 第六轮）

EP01=dev，EP02/03=test；Qwen3 1.7B + FA，60 s 自切；`episode_benchmark.py --global-offset`。

| split | 集 | CER | match | P50 起 | P90 起 | ±100ms | 可靠 P90 | offset | 自动修 / 截短 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3643 | 49/276 | 129 ms | 1897 ms | 46.9% | 424 ms | — | 13 / 0 |
| test | EP002 | 0.2581 | 79/370 | 69 ms | 559 ms | 65.8% | 361 ms | -0.37 s | 19 / 0 |
| test | EP003 | 0.2698 | 79/317 | 46 ms | 1743 ms | 70.9% | 410 ms | -0.40 s | 9 / 0 |
| test 合计 | 2 | 0.2633 | 158/687 | 57.5 ms | 1151 ms | 68.3% | 385 ms | — | 28 / 0 |

- 整集偏移（既有算法，本轮接进 `align_cues` 默认）把 test P50 从 400 ms 降到 57.5 ms。
- 结尾 display linger 保留后，原本 113 条「会缩短结尾」的自动修复降为 0；起点指标不变。
- 可靠 P90 385 ms 仍高于 300 ms 目标；主因是短语气词/重复台词歧义和 FA 零时长，需要 P3 VAD/分段重对齐。

## 7. clear 后恢复入口

```powershell
# 1) 主故事音频（95–1240s，三集同结构；覆盖最后一条对白）
python -c "from lib.whisper_utils import extract_audio_wav; extract_audio_wav(r'<EP>.mkv', r'temp\asr\digimon_epXXX_main.wav', ss=95, duration=1145, prefer_lang='ja')"

# 2) Qwen3 1.7B 转写（本地模型，60s chunk）
.\.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\asr\digimon_epXXX_main.wav --asr models\Qwen3-ASR-1.7B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\asr\digimon_epXXX_main.qwen3.json `
  --srt temp\asr\digimon_epXXX_main.qwen3.srt

# 3) held-out 基准（只读；dev/test 分组）
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --global-offset --output temp\reports\digimon_benchmark_final.md `
  --json-out temp\reports\digimon_benchmark_final.json

# 4) 时间轴诊断（无参考也可跑；有参考时加 --srt + --shift -95）
python scripts\eval\diagnose_timeline.py `
  --asr-json temp\asr\digimon_epXXX_main.qwen3.json `
  --srt temp\references\digimon\EPXXX.main.srt --shift -95
```

当前状态（2026-10-18 第六轮）：

```text
EP01/02/03 视频 ✅；日文 CC 54 集 ✅；EP01 CC 同步已人工确认，EP02/03 待确认
主故事窗口 [95,1240]；ASR 缓存 temp/asr/digimon_ep00X_main.qwen3.json
基准：temp/reports/digimon_benchmark_final.{json,md}
协议：eval/protocol.md；ADR-009（参考=考卷）
测试：1362 passed / 79.6%
```


