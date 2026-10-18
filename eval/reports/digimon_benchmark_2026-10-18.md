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
- 整集偏移（既有算法，本轮接进 `align_cues` 默认）：test 原始 P50 400 ms → 57.5 ms，±100 ms 5.1% → 68.3%。
- 结尾 display linger 保留：旧逻辑三集 113 条自动修复会缩短结尾，现为 0；起点 P50/P90 不变。旧/新 auto：37/59/47 → 13/19/9。
- 负结果：segment-start overlong trim 仅 dev 提升、test 中性 → opt-in；cursor 回看增加 match 但 P90 变差 → 不采纳。
- 未达标：可靠 P90 385 ms > 300 ms；剩余误差来自短语气词/重复台词歧义与 FA 零时长，需 P3 VAD/分段重对齐。

复现（从工作区根目录）：

```powershell
python scripts\eval\episode_benchmark.py --manifest eval\manifest.digimon.json `
  --global-offset --output temp\reports\digimon_benchmark_final.md `
  --json-out temp\reports\digimon_benchmark_final.json
```
