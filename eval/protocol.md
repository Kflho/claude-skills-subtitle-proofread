# 评测协议：日语 CC 是考卷，不是教材

> 目的：真实工作时不一定有日语参考字幕；因此参考字幕只能用来**打分**，
> 不能用来反推阈值、规则或 prompt。任何改动必须先在 dev 上完成，
> test 集冻结，按 release candidate 跑一次。

## 1. 数据划分

| 集 | split | 用途 | 备注 |
|---|---|---|---|
| EP001 | `dev` | 冒烟、机制调试、报告格式验证 | 早期已看过 EP01 指标，不能当 test |
| EP002 | `test` | 冻结后的验收对比 | 跑之前不改参数 |
| EP003 | `test` | 冻结后的验收对比 | 同上 |

- 参考字幕：`temp/references/digimon/EP00X.main.srt`（已清洁、主故事窗口）。
- 音频窗口：`[95, 1240]` 秒（三集同结构；覆盖最后一条对白，不含完整 ED）。
- ASR 缓存：`temp/asr/digimon_ep00X_main.qwen3.json`。
- 评测器：`scripts/eval/episode_benchmark.py`（只读，不写阈值/配置）。

## 2. 什么算“真的优化”

同一 split、同一 ASR 输入、同一评测器下，同时报告：

1. **CC CER**（macro + micro）：文本层，参考字幕含字幕卡/版本写法差异，只看相对变化；
2. **cue 对齐**：match 数、起点 P50/P90、±100ms、clean_ratio；
3. **时间轴内部指标**：token 零时长率、最长塌缩 run、overlong 率；
4. **RTF / 耗时**（只作回归，不拿来换精度）。

判定规则：

- 主指标是 **test split 的 CER 与起点 P50/P90**；dev 只看方向，不写结论；
- 精度提升必须不以“更多假自动修复”为代价：clean_ratio、`fix_time` 自动数也要一起看；
- 若 test 无提升或退化：回退机制，不在 test 上继续调参；
- 小数级别的波动（<1% CER、<20ms P50）不算优化，除非有机制解释。

## 3. 允许的证据来源

| 决策 | 允许的依据 | 禁止 |
|---|---|---|
| 零时长/超长 token 修复 | 音频能量、VAD、局部语音速率、token 自身分布 | “这样 EP01 覆盖率更好看” |
| chunk 长度 | 无参考的内部指标（零时长率、RTF）+ 既有 ADR-008 实测 | 用 CC 边界误差搜 45/60/90 |
| 质量阈值 | 物理定义（秒/字、连续 token 数）+ 政策注释 | 用 test CER 反推 0.05/25 等常数 |
| mapper 窗口 | 匹配稳定性（窗口扰动自洽）、cue 间隔分布 | 直接最小化 CC 边界 P90 |
| 翻译 prompt | 原文、glossary、场景上下文 | 把参考字幕整段塞进 prompt 当答案 |

### 3.1 当前启发式/常数的证据台账

| 位置 | 当前值/规则 | 证据 | 验证 |
|---|---|---|---|
| `qwen3_worker._fill_zero_durations` | 只在 gap ≤ max(3 s, 0.6×run) 内补零 | 早期占位；**无音频证据，属遗留** | 三集零时长 3.2–9.4%，未作为验收依据；彻底替换留 P3 |
| `qwen3_worker` chunk | 60 s 自切 | ADR-008：180 s 内部切分零时长 48% vs 60 s 8% | Sari/EP01–03 机制回放 |
| `TimelinePolicy.degrade_*` | 零时长 ≥5%、run ≥4、overlong ≥5% | 政策定义：20 条里 1 条无时间；4 unit ≈0.64 s；6×局部速率 | 单测锁行为，不锁分数 |
| `TimelinePolicy.fallback_*` | 零时长 ≥50%、单 run ≥10% unit、overlong ≥20% | 相对阈值，避免按集长度改变含义 | 单测 + EP01–03 均不回退（segment 由同一批 token 生成） |
| `TimelinePolicy.repair_overlong` | 仅 segment 起始 token，>max(2 s, 6×rate) | 机制：FA 把前导静音吸进句首 token；局部中位速率 | dev 严格变好，test 中性 → **opt-in** |
| `align_repair` display linger | 0 < end_error ≤ max_time_shift 且起点可修 | EP01–03 CC 分布：起点匹配好时 end 中位 +0.53/+0.33/+0.19 s | 三集 113→0 条截短，起点指标不变 |
| `align_cues` 整集偏移 | **opt-in，默认关**；只在有独立证据（不同剪辑/帧率）时用 | 用户人工复核 Digimon 视频/CC 无偏移；估计值 -0.37/-0.40 s 是 ASR 起点 vs CC 显示起点的系统差 | 不要用 offset-corrected 数字当验收；局部偏移原型同样不采纳 |
| `_select_window` search | ±6 s | 先验 + cue 间隔；未用 CC 调 | 待做窗口扰动自洽诊断 |
| cursor 回看 | 未启用 | 实测增加 match 但 P90/±100 ms 变差 | 负结果，不采纳 |

## 4. 变更流程

1. 写清“这条启发式原来凭什么、现在换成什么证据”；
2. 在 dev 上跑 `episode_benchmark.py`，记录 before/after；
3. 补测试：锁机制/行为，不锁 dev 分数；
4. 冻结代码和 ASR 缓存，跑一次 test；
5. 把 before/after 原样写进 `docs/tech-route-progress.md`，注明 split；
6. 若 test 退化，回退并在文档里保留失败记录。

## 5. 防过拟合硬约束

- 禁止按集特判（`if episode == 'EP002'`）；
- 禁止把参考字幕文本回流到 ASR/对齐/翻译输入（评测过滤除外）；
- 禁止在 test 集上迭代阈值；test 只跑 release candidate；
- 评测脚本不得写回 manifest、ASR JSON 或任何参数文件；
- 报告必须带 `split`、样本数、ASR 缓存路径和 git SHA，缺一不可。

## 6. 已知偏差（报告里必须保留）

- CC 含说话人标签/字幕卡/歌词，清洁后仍有非对白；CER 会偏高；
- CC 写法（片假名、省略语气词）与 ASR 不同，文本差异不全是 ASR 错；
- 主故事窗口边界由人工切；EP01 已由用户确认与视频同步，EP02/03 尚未逐句人工核对；
- **不要用全局偏移吸收 onset bias**：用户人工复核确认 Digimon EP02/03 视频/CC 没有真实偏移；
  raw 才是验收口径。估计的 -0.37/-0.40 s 是 ASR 起点与 CC 显示起点的系统差；全局/局部偏移
  都会让字幕整体提前，不作为默认修正。
- `match` 只统计文本相似度 ≥0.85 的 cue，低相似 cue 不进 P50/P90，样本量要一起看。
