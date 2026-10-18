# 数码宝贝 EP01–03 held-out 基准（2026-10-18 第六轮）

> 参考日文 CC 只作考卷，不参与调参；协议见 [`../protocol.md`](../protocol.md) / ADR-009。
> 数据：`episode_benchmark.py --manifest eval/manifest.digimon.json --global-offset`。
> ASR：Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B，60 s 自切；主故事窗口 `[95,1240]`。
> 缓存：`temp/asr/digimon_ep00X_main.qwen3.json`（本机，不入库）。

| split | 集 | CER | ref chars | cues | match | P50 起 | P90 起 | ±100 ms | 可靠 n | 可靠 P90 | offset | auto | 截短 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | EP001 | 0.3643 | 2056 | 276 | 49 | 129 ms | 1897 ms | 46.9% | 35 | 424 ms | — | 13 | 0 |
| test | EP002 | 0.2581 | 4107 | 370 | 79 | 69 ms | 559 ms | 65.8% | 68 | 361 ms | -0.37 s | 19 | 0 |
| test | EP003 | 0.2698 | 3336 | 317 | 79 | 46 ms | 1743 ms | 70.9% | 71 | 410 ms | -0.40 s | 9 | 0 |
| test 合计 | 2 | 0.2633 micro | 7443 | 687 | 158 | 57.5 ms | 1151 ms | 68.3% | 139 | 385 ms | — | 28 | 0 |

- 「可靠」= match 且 cue 字数 ≥3、confidence ≠ low；1–2 字语气词歧义，不混入主 P90。
- 表内 P50/P90/±100ms 是**按集平均**；合并样本（pooled）test：all P50 57.5 ms / P90 951 ms / ±100ms 68.4%，reliable P50 50 ms / P90 377 ms / ±100ms 74.8% / ±200ms 82.0%。
- 若按全部 687 条 CC cue 作分母，±100ms 确认准确约 108/687 = 15.7%；其余 529 条不是时间错，而是文本/字幕卡/短语气词导致无法匹配，不能直接算成打轴失败。
- 整集偏移（既有算法，本轮接进 `align_cues` 默认）：test 原始 P50 400 ms → 57.5 ms，±100 ms 5.1% → 68.3%（按集平均；pooled 5.2% → 68.4%）。
- 结尾 display linger 保留：旧逻辑三集 113 条自动修复会缩短结尾，现为 0；起点 P50/P90 不变。旧/新 auto：37/59/47 → 13/19/9。
- 负结果：segment-start overlong trim 仅 dev 提升、test 中性 → opt-in；cursor 回看增加 match 但 P90 变差 → 不采纳。
- 未达标：可靠 P90 385 ms > 300 ms；剩余误差来自短语气词/重复台词歧义与 FA 零时长，需 P3 VAD/分段重对齐。

### 改动前后（test split，EP02+03）

| 指标 | `align_cues` 原始（无偏移） | `align_cues` + 整集偏移 |
|---|---:|---:|
| matched | 153/687（22.3%） | 158/687（23.0%） |
| 起点 P50（按集平均） | 400 ms | 57.5 ms |
| 起点 P90（all matched） | 1196 ms | 1151 ms |
| ±100 ms（all matched） | 5.1% | 68.3% |
| 可靠 n | 133 | 139 |
| 可靠 P90 / ±100 ms | 510 ms / 5.1% | 385 ms / 74.8% |
| 自动修复 / 其中截短结尾 | 106 / 82 | 28 / 0 |

> `align_cues` 之前不做整集偏移；`fix_by_asr` 管线本来就会做，所以**管线路径的起点精度今天没有变化**，
> 今天新增的是 CLI 默认偏移 + 结尾不再截短 + 零时长安全降级。不要把 CLI 的提升误报成管线提升。

复现（从工作区根目录）：

```powershell
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --global-offset --output temp\reports\digimon_benchmark_final.md `
  --json-out temp\reports\digimon_benchmark_final.json
```
