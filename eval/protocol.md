# 评测协议：日语 CC 是考卷，也是边界测试标准

> 目的：真实工作时不一定有日语参考字幕；因此参考字幕只能用来**打分**，
> 不能用来反推阈值、规则或 prompt。任何改动必须先在 dev 上完成，
> test 集冻结，按 release candidate 跑一次。
>
> **2026-10-18 用户决定**：日语 CC 的边界很准，**直接作为边界测试标准**，
> 不再另建人工金标。P50/P90 按 raw（不做偏移修正）直接判定。

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
2. **cue 对齐**：match 数、起点 P50/P90、±100ms、±300ms、clean_ratio；
3. **signed 起点误差**：P10/P50/P90、early share、P90−P10（区分系统偏差与离散）；
4. **文本分解**：字符覆盖率、matched-span CER（≥0.85 / ≥0.5）、无匹配字符数；
5. **时间轴内部指标**：token 零时长率、最长塌缩 run、overlong 率、空洞（`lost_spans`）；
6. **P90 归因**：>300 ms 的 matched cue 按 zero_span / collapsed / stretched / short / low-conf / ambiguous / repeat / plain 计数；
7. **RTF / 耗时**（只作回归，不拿来换精度）。

判定规则：

- 主指标是 **test split 的 CER 与起点 P50/P90**；dev 只看方向，不写结论；
- 精度提升必须不以“更多假自动修复”为代价：clean_ratio、`fix_time` 自动数也要一起看；
- 若 test 无提升或退化：回退机制，不在 test 上继续调参；
- 小数级别的波动（<1% CER、<20ms P50）不算优化，除非有机制解释。

### 2.1 怎么读这些数字（第八轮新增）

- **signed P50 是当前对齐的系统误差，不是参考噪声**。用户已确认日语 CC 边界可作
  测试标准，所以 test 集 90% 的 matched cue 起点早于 CC、signed P50 −0.37/−0.40 s
  就是对错的实例：**P50/P90 直接按 CC 判定，当前未达标**。用参考估计出的偏移去
  平移输出属于自证循环（估计和打分用同一批 cue），禁止；偏差要在对齐/音频侧修。
  已复现：同一段音频重新转写，token 时间与缓存一致（±0.02 s），说明不是缓存过期
  或抽音频偏移，而是模型在该素材上的起点偏早；EP001 只有 −55 ms，需按集解释。
- **逐 cue start fix 也会继承这个偏差**：关掉整集偏移只停平移，不改变逐条起点；
  在偏差修掉前，`fix_by_asr` 的起点自动修复会把字幕整体提前约 0.4 s。
- **P90 只在 matched 子集上算**，不覆盖全集：Qwen3 三集字符覆盖率 42–45%
  （Whisper 46–52%），六成 CC 文字没有可靠匹配。报告必须把覆盖率与样本量
  放在 P90 旁边；覆盖率提高时 P90 可能变差（更多边缘匹配进来），这不是退化。
- **matched-span CER 不能和全局 CC CER 比大小**：前者只统计有匹配的 cue 且
  不计上下文插入，回答“已匹配对白里错多少”（Qwen3 test 0.079）；全局 CER
  回答“整份 CC 差多少”（0.263）。两者要一起看。
- **空洞（collapsed run + gap）是 P3 的输入**，不是 P2 的修复对象：
  EP001/02/03 分别有 19/7/9 个空洞，累计 229/122/96 s 的音频没有时间轴。

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
| `align_cues` 整集偏移 | **opt-in，默认关**；只在有独立证据（不同剪辑/帧率）时用 | 用户确认 CC 边界即测试标准；估计值 −0.37/−0.40 s 是当前对齐的起点缺陷 | 不要用参考自估的偏移平移输出（自证循环）；修偏差要对齐/音频侧 |
| `_select_window` search | ±6 s | 先验 + cue 间隔；未用 CC 调 | 待做窗口扰动自洽诊断 |
| cursor 回看 | 未启用 | 实测增加 match 但 P90/±100 ms 变差 | 负结果，不采纳 |
| `fix_by_asr` 整集偏移 | **默认关**（`ASR_GLOBAL_OFFSET=0`），`--asr-global-offset` opt-in | 同上：偏移估计来自参考侧与 ASR 的系统差，平移输出会自证 | 只写进报告 `global_offset` + `global_offset_applied`，不改默认 |
| `liberal` cue 级速率 trim（`end − rate×chars` 估起点） | **未启用** | 三集模拟：dev rel P90 424→1086 ms、test 520→850 ms | 负结果：用 end 锚点会把 display linger 带进起点 |
| 分段重对齐（同一 FA，重跑塌缩窗口） | **未启用** | dev EP001 20.56 s 空洞重跑：19 unit 中 11 个零时长、「門」吃 8.8 s；EP002 51 s 空洞（仅诊断）「フィシーズ」吃 41 s；5–6 s 短窗口被 clip 起点夹住 | 负结果：需要 P3 VAD 边界，不能只重跑 FA |
| `lost_spans` 诊断 | gap ≥3 s 且有塌缩 run / 单 token >5 s 且 ≤3 字 | 物理定义 + 实测空洞（EP001–03：19/7/9 个） | 只进报告，不作修复依据 |

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
- 主故事窗口边界由人工切；三集均由用户确认 CC 与视频同步，**CC 边界即测试标准**；
- **对齐的起点系统性偏早（不是参考噪声）**：test 集 90% matched cue 的 ASR 起点早于
  CC，signed P50 −0.37/−0.40 s；同段音频重转写复现（缓存不是过期/抽音频偏移）。
  **不要用全局偏移吸收它**：参考自估偏移再平移输出是自证循环；raw 才是验收口径。
- **同一季度内偏差并不统一**：EP001（dev）signed P50 −55 ms、early 63%，
  EP002/03（test）signed P50 −371/−397 ms、early 89–92%。任何全局常数都不能当跨集修正，
  偏差要按集/按段在音频侧解决。
- **超过 2 s 的起点“误差”多半是错误匹配**，不是边界误差：EP001 有 5 条、EP002/03 各 6 条
  |start err| > 2 s，文本能 1.0 匹配到别处的重复台词/字幕卡；它们不进自动修复，只在 review。
- `match` 只统计文本相似度 ≥0.85 的 cue，低相似 cue 不进 P50/P90，样本量要一起看。
- **字符覆盖率是关键分母**：Qwen3 三集只匹配到 42–45% 的 CC 字符，其余 55–58% 是
  无匹配（字幕卡、歌词、被 FA 吞掉的台词）。P90 只描述这 42–45% 的对白，不能外推整集。
