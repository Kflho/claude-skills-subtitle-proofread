# 金标素材登记

> **用户指定（2026-10-18）**：验收作品改为 **数码宝贝（Digimon Adventure）**。
> **素材目录**：`D:\videos\Animation\TV\数码宝贝`
> 本文件是素材台账；`eval/manifest.digimon.json` 是评测清单草稿。

## 1. 目录现状

| 集 | 视频 | 中文字幕 | 日文参考字幕 | 备注 |
|---|---|---|---|---|
| 01 漂流？冒険の島！ | ✅ `01、漂流？冒険の島！.mkv` | ✅ `.sc.ass` / `.tc.ass` | ✅ `日语字幕\…S01E01…ja[cc].srt` | 视频+日文 CC 到齐 |
| 02 爆裂進化！グレイモン | 🟡 `.mkv.downloading`（0 B） | ✅ `.sc.ass` / `.tc.ass` | ✅ `…S01E02…ja[cc].srt` | 下载中 |
| 03 蒼き狼！ガルルモン | 🟡 `.mkv.downloading`（0 B） | ✅ | ✅ `…S01E03…ja[cc].srt` | 下载中 |
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

## 5. 待接收压缩包（2026-10-18）

- 路径：`C:\Users\54238\Desktop\数码宝贝.zip.downloading`
- 22:07 已 **20.54 GB**（用户说明：下载完成后解压到 `D:\videos\Animation\TV\数码宝贝`）。
- EP02/03 的 `.mkv.downloading` 目前 0 B（22:13 检查），等完成后再开跑三集。
- 解压后重新盘点视频集数/命名，更新 `eval/manifest.digimon.json` 与本文件表格；如内容有变化以解压后为准。

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

**结论**：首轮数字不能当 P2-14 结果。三集评测改用 **主故事窗口 95–1233s**，
参考按「只保留对白」过滤（含字幕卡/角色名剔除），并单独记录 ASR 漏掉的短语气词；
token 时间轴问题需要单独修（或改用分段重对齐）。

## 7. clear 后恢复入口

```powershell
# 0) 看视频是否下完（只认完整 .mkv，不认 .downloading）
Get-ChildItem -LiteralPath 'D:\videos\Animation\TV\数码宝贝' -Filter '*.mkv*'

# 1) 主故事音频（95–1233s，三集同结构）
ffmpeg -y -v error -ss 95 -t 1138 -i "<EP02/EP03.mkv>" -vn -ac 1 -ar 16000 `
  -c:a pcm_s16le temp\asr\digimon_epXXX_main.wav

# 2) Qwen3 1.7B 转写（本地模型）
.\.venv-qwen\Scripts\python.exe scripts\asr\qwen3_transcribe.py `
  --audio temp\asr\digimon_epXXX_main.wav --asr models\Qwen3-ASR-1.7B `
  --aligner models\Qwen3-ForcedAligner-0.6B --timestamps `
  --output temp\asr\digimon_epXXX_main.qwen3.json `
  --srt temp\asr\digimon_epXXX_main.qwen3.srt

# 3) P2 对齐（主故事参考需相对音频起点 -95s；EP001 已生成 digimon_ep001_main_ref.srt）
python scripts\asr\align_cues.py --srt temp\references\digimon\digimon_epXXX_main_ref.srt `
  --asr-json temp\asr\digimon_epXXX_main.qwen3.json `
  --output temp\reports\digimon_epXXX_main_align.json
```

当前状态（2026-10-18 22:15）：

```text
EP01 视频 ✅，用户已人工核对 CC 同步；EP02/03 .mkv.downloading 0 B 🟡
日文 CC S01E01–S54 ✅；已清洁到 temp/references/digimon/
主故事参考：EP001/002/003.main.srt 已切；EP001 另有 -95s 平移版
压缩包： C:\Users\54238\Desktop\数码宝贝.zip.downloading ≈20.54GB 🟡
测试：1306 passed / 79.0%；提交 141b1f1
```


