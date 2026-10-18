# 数码宝贝 EP01–03 held-out 基准（2026-10-18 第六轮）

> 参考日文 CC 只作考卷，不参与调参；协议见 [`../protocol.md`](../protocol.md) / ADR-009。
> **用户人工复核结论：视频/CC 没有真实偏移，按无偏移评估。** 此前 `estimate_global_offset`
> 得到的 -0.37/-0.40 s 是 **ASR 起点 vs CC 显示起点** 的系统差，不是视频偏移；不再作为默认修正。
> 数据：`episode_benchmark.py --manifest ...`（默认无偏移）。
> ASR：Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B，60 s 自切；主故事窗口 `[95,1240]`。
> 缓存：`temp/asr/digimon_ep00X_main.qwen3.json`、`.whisper.json`（本机，不入库）。

## 1. Qwen3 1.7B + FA（主口径，raw / 无偏移）

| split | 集 | CER | ref chars | cues | match | P50 起 | P90 起 | ±100 ms | 可靠 n | 可靠 P90 | auto | 截短 | token zero |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3643 | 2056 | 276 | 49 | 129.0 ms | 1896.6 ms | 46.9% | 35 | 424.2 ms | 13 | 0 | 9.4% |
| test | EP002 | 0.2581 | 4107 | 370 | 79 | 394.0 ms | 837.2 ms | 7.6% | 69 | 494.4 ms | 21 | 0 | 3.2% |
| test | EP003 | 0.2698 | 3336 | 317 | 74 | 406.0 ms | 1554.8 ms | 2.7% | 64 | 526.0 ms | 17 | 0 | 6.3% |
| test 合计 | 2 | 0.2633 micro | 7443 | 687 | 153 | 400.0 ms | 1196.0 ms | 5.1% | 133 | 510.2 ms | 38 | 0 | 4.7% |

- 「可靠」= match 且 cue 字数 ≥3、confidence ≠ low；1–2 字语气词歧义，不混入主 P90。
- 表内 P50/P90/±100 ms 是**按集平均**；合并样本（pooled）test：all P50 402 ms / P90 1059 ms /
  ±100 ms 5.2%；reliable P50 392 ms / P90 521 ms / ±100 ms 5.3%。
- 若按全部 687 条 CC cue 作分母，±100 ms 确认准确约 36/687 = 5.2%；其余不是时间全错，
  而是文本/字幕卡/短语气词导致无法匹配，不能直接算成打轴失败。
- **P90 510 ms（可靠）没达到 300 ms 目标**。这就是当前真实打轴水平，不能用“整集偏移”把它做低。

## 2. Whisper large-v3 q5 A/B（同一参考、同一窗口、raw）

| split | 集 | CER | match | P50 起 | P90 起 | ±100 ms | 可靠 n | 可靠 P90 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3069 | 52/276 | 559.5 ms | 2568.9 ms | 9.6% | 39 | 1135.0 ms |
| test | EP002 | 0.2089 | 96/370 | 824.0 ms | 1577.5 ms | 3.1% | 84 | 1447.9 ms |
| test | EP003 | 0.1981 | 99/317 | 689.0 ms | 1728.9 ms | 4.0% | 88 | 1202.7 ms |
| test 合计 | 2 | 0.2041 micro | 195/687 | 756.5 ms | 1653.2 ms | 3.5% | 172 | 1325.3 ms |

结论：

- **文本：Whisper 更好**（test CER 0.204 vs Qwen3 0.263，match 195 vs 153）。
- **打轴：Qwen3 + FA 明显更好**（test P50 400 vs 757 ms，可靠 P90 510 vs 1325 ms）。
- Whisper 只有 segment 级时间戳，边界精度受段长限制；Qwen3 有 token 时间轴，但文本略差。
- 不是“谁全面更好”，而是 **文本 / 时间轴两个维度各有胜负**；混合方案（Whisper 文本 + Qwen3-FA 对齐）是后续候选，不在本轮实现。

## 3. 今天真正改变了什么

| 改动 | 证据 | 效果 |
|---|---|---|
| 结尾 display linger 保留 | 参考 CC 的 end 普遍晚于 ASR 语音结束（起点匹配好时中位 +0.19～0.53 s） | 三集原本 **106 条**自动修复会缩短结尾，现在 **0 条**；起点 P50/P90 不变 |
| 零时长 span 不自动修 | collapsed timestamp 没有边界证据 | EP01 的 2 条 collapsed match 从 auto 降为 review |
| 质量诊断 / segment 兜底 | token 零时长 run、overlong、质量分 | EP01–03 均保留 token（segment 更差），只告警不换源 |
| 时间轴诊断工具 | 无参考可复现 | `diagnose_timeline.py` 可查零时长/overlong |

## 4. 被否掉的“优化”

- **整集偏移（-0.37/-0.40 s）**：用户人工复核确认视频/CC 没有偏移；该值是在吸收 ASR 起点与
  CC 显示起点的系统差。把它当默认修正会让字幕整体提前，已从 `align_cues` 默认移除，
  只保留 `--global-offset` 作为“确有独立证据的整文件漂移”诊断开关。
- **局部偏移原型**：同样会吸收这个系统差，不采纳。
- **segment-start overlong trim**：仅 EP01 dev 提升，EP02/03 test 中性，保持 opt-in。
- **cursor 回看**：增加 match 但 P90/±100 ms 变差，不采纳。

## 5. 还差什么

- 可靠 P90 510 ms > 300 ms；主因是短语气词/重复台词歧义和 FA 零时长。
- 需要 P3 VAD 或分段重对齐，不能继续在 test 上调阈值/常数。
- dialogue-only CER 未做；CC 字幕卡/歌词仍会抬高 CER。
- EP02/03 的 CC 与视频同步以用户人工结论为准：无偏移。

复现（从工作区根目录）：

```powershell
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --output temp\reports\digimon_qwen3_raw.md `
  --json-out temp\reports\digimon_qwen3_raw.json

python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.whisper.json `
  --output temp\reports\digimon_whisper_raw.md `
  --json-out temp\reports\digimon_whisper_raw.json
```
