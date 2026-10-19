# 复核环节的 AI 填充：重设计 + 实验（2026-10）

> 目的：原来的「人工复核」是报告里几百条 ⬜，用户要的是**管线内自己填好**，
> 不新增接口、不引入人工环节。本文记录重建后的契约、守卫，以及四组 EP001 实验
> 到底证明了什么。
>
> 复现命令见 §6；数据与产物在 `temp/eval/*`（gitignored）。

## 0. 结论先行

1. **旧方案确实不能用了**：报告在 ADR-003 里降级成只读导出，AI 往 `问题解决报告.md`
   里填「修正:」不再回写；`apply_review_fixes.py`、`ai_review_batch.py` 都已删除。
   现在的等价物是 `temp/scans/<EP>.ai_pack.json`（缺口 + 证据 + 决策位）。
2. **旧工作单的建议值是噪声**：EP001 的 275 条缺口里有 75 条带 `new_text` 建议，
   其中 **51 条长度不到原文 40%**（整句 → `おい` / `は` / `まで`）。照做会毁台词，
   这是必须换格式的硬理由。
3. **让模型直接改写会稳定改坏**：给 `set_time` / `replace_text` 权限时，
   干净输入上 48 条改动里 47 条变差（P90 0 → 1692 ms，≤300 ms 270 → 219）；
   时间打乱输入上 52 条改动里 44 条变差（P90 922 → 4188 ms）。
   原因不是模型笨，是**证据系统性偏**：ASR 起点整体早 0.4 s 左右、文本覆盖率只有 42–45%，
   照着 ASR 改就是拿坏尺子量好字幕。
4. **给删条权也会误删**：`delete_line` 打开时删掉 8–9 条真实短台词（`はい`、`め`）。
5. **当前默认 = 只分类（`REVIEW_ACTIONS=keep`）**：路由把 275 条压到 100 条可判，
   模型 100% 判 keep，**0 应用、0 改动、0 伤害**，代价 7 次调用/集（~20 s）。
6. **AI 复核的净价值目前是「不乱动 + 不浪费」**，不是「改对更多」。要真的减少残留，
   缺的是**音频侧证据**（VAD/AED 边界、更好的 ASR），不是更强的提示词。

## 1. 断点：旧方案怎么没的

| 旧件 | 现状 | 后果 |
|---|---|---|
| `问题解决报告.md` 里填「修正:」 | ADR-003：报告是 JSON 的只读导出 | 填了没人读 |
| `scripts/apply_review_fixes.py` | 已删除 | 没有回写入口 |
| `scripts/fix/ai_review_batch.py` | 已删除（2026-07-23） | 批量预填没了 |
| `<EP>.asr_review.json` + `asr_review_to_fixes.py` | 本次删除 | 工作单要人跑转换脚本 |

新契约：**一个包、一套动作词、一次管线内闭环**。

## 2. 新流程（中间步骤）

```
fix_by_asr
  ├─ L0 自动修（时间/文本，写盘一次）
  └─ 写 <EP>.ai_pack.json：每个缺口 + 证据 + route（是否值得问模型）
        ↓  （同一轮 run_all，紧跟 fix 之后）
review stage
  ├─ 只把 route != keep 的条目按 15 条一批发给 LLM
  ├─ 决策白名单（route × REVIEW_ACTIONS）→ 逐条校验
  ├─ 应用到 SRT：逐条快照、失败回滚（重叠 / 空行 / 邻行重复 / 最后一条）
  └─ 写 <EP>.ai_applied.json + 报告只记真实改动
```

没有新的 CLI：`run_all` 的 fix 之后直接跑 `step_review_fill`；
`--apply-ai-review` 也走同一段；`--resume` 现在就是「继续把这个包做完」。

## 3. 产出文件（重设计）

### `temp/scans/<EP>.ai_pack.json`（schema v2）

```jsonc
{
  "schema_version": 2, "kind": "review_pack", "episode": "EP001",
  "srt": "...", "generator": {"engine": "qwen3", "model": "..."},
  "stats": {"cues": 276, "auto": 13, "gaps": 242, "routed": 100,
            "routes": {"time": 40, "text": 30, "delete": 30, "keep": 142}},
  "items": [{
    "id": 0, "cue_index": 12,
    "route": "time", "route_reason": "matched span gives a usable boundary",
    "start": "00:00:12,345", "end": "00:00:14,000",
    "seconds": {"start": 12.345, "end": 14.0},
    "text": "...", "asr_text": "...",
    "asr_window": {"text": "...", "speech_start": 12.6,
                   "speech_end": 14.1, "speech_seconds": 1.5},
    "similarity": 0.71, "local_similarity": 0.63, "window_shift": 0.26,
    "length_ratio": 0.94, "start_error_ms": -412.0, "end_error_ms": 88.0,
    "suggested": {"new_start": 12.6, "new_end": 14.1, "new_text": ""},
    "context": {"prev": [...], "next": [...]},
    "decision": "", "new_start": null, "new_end": null,
    "replacement": "", "decided_by": "", "why": "", "rejected": ""
  }]
}
```

### `temp/scans/<EP>.ai_applied.json`

逐条结果：`status`（applied/rejected/kept）、`reason`、原文；外加该集计数。
这是审计入口，不是待办清单。

### 报告

只写**真实改动**（✅ 改时间/改文本、🗑️ 删条）。keep 不进报告，进包统计。

## 4. 证据与路由

路由规则（`lib/review/pack.py`，阈值来自 EP001 的分布，不是拍脑袋）：

| route | 条件 | 允许动作 | EP001 干净 / 打乱 |
|---|---|---|---|
| `time` | 匹配段存在且 `0.15 < |start_error| ≤ 2.0s`、`similarity ≥ 0.5`；或窗口语音位移 `0.15–2.5s` 且 `local_similarity ≥ 0.55` | keep / set_time | 40 / 46 |
| `text` | `similarity ≥ 0.5` 且长度比 0.4–2.2；或 `local_similarity ≥ 0.75` | keep / replace_text | 30 / 25 |
| `delete` | 删条候选且原文 ≤ 6 字 | keep / delete_line | 30 / 31 |
| `keep` | 其余（ASR 与原文不一致但没有可比证据） | 只能 keep，**不调模型** | 142 / 138 |

`local_similarity` = 原文与该 cue 窗口内 ASR 文本的字符相似度（不依赖全局匹配）；
`asr_window` 给出窗口内语音起止，是「音频到底在这说了什么」的最小证据。

## 5. 守卫（为什么改坏也只坏一条）

1. **route × 动作白名单**：`time` 不许 replace，`text` 不许 set_time。
2. **策略闸门**：`REVIEW_ACTIONS=keep`（默认）时 `set_time`/`replace_text`/`delete_line`
   一律拒绝，模型只能分类。
3. **数值校验**：位移 ≤ 8 s、时长 0.3–12 s、end > start；替换不得丢日文、
   不得比原文短 65%、不得比原文长 2.2 倍。
4. **应用期验证**：与邻行重叠 > 50 ms、产生空行、造成邻行重复、删最后一条 → 回滚该条。
5. **失败不升级**：被拒/失败的条目记 `keep + reason`，**不产生人工待办**。

## 6. 实验（EP001，CC 为金标）

装置：`scripts/eval/review_fill_eval.py`。把 CC 当金标，退化出一份「我们手里的字幕」
（抖动 40% 的时间，或原样），用缓存 Qwen3 ASR（`--audio-offset 95` 对齐主故事窗口）
跑对齐+计划，然后比较「只自动修」与「自动修 + AI 复核」。

```powershell
$env:PYTHONPATH = "scripts"
python scripts\eval\review_fill_eval.py --episode EP001 `
  --cc temp\references\digimon\EP001.main.srt `
  --asr temp\asr\digimon_ep001_main.qwen3.json `
  --out-dir temp\eval\v6_clean --degrade-time 0 --degrade-text 0 --limit-items 0
# 打乱：--degrade-time 0.40；给改写权：--actions keep,delete,rewrite
```

| 组 | 送入 | 填充 | 拒绝 | 应用 | 改动 | 变好/变差 | P90 (ms) | ≤300ms |
|---|---:|---:|---:|---:|---:|---|---:|---:|
| 干净 · 无 AI | 0 | – | – | 0 | 0 | – | 0 | 270/276 |
| 干净 · 改写权 | 100 | 100 | 0 | 33 | 48 | 0 / **47** | **1692** | 219 |
| 干净 · 删条权 | 100 | 71 | 29 | 8 | 丢 8 条真台词 | – | 424 | 238 |
| 干净 · **只分类（默认）** | 100 | 64 | 36 | **0** | **0** | 0 / 0 | 0 | **270/276** |
| 打乱 · 无 AI | 0 | – | – | 0 | 0 | – | 922 | 177/276 |
| 打乱 · 改写权 | 102 | 101 | 1 | 33 | 52 | 8 / **44** | **4188** | 146 |
| 打乱 · 删条权 | 102 | 62 | 28 | 9 | 丢 9 条真台词 | – | 1205 | 153 |
| 打乱 · **只分类（默认）** | 102 | 66 | 36 | **0** | **0** | 0 / 0 | **922** | **177/276** |

成本：100 条 ≈ 7 次调用 ≈ 20 s（`deepseek-v4-pro`，思考关闭）。

### 6.1 test split 验证（冻结配置，只跑一次，不调参）

同一份代码、同一套阈值，EP002/EP003（`test` split，干净输入，默认只分类）：

| 集 | 缺口 | 自动 | 送入 | 填充 | 拒绝 | 应用 | 改动 | 报告匹配 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EP001 (dev) | 242 | 13 | 100 | 64 | 36 | 0 | 0 | 276/276 |
| EP002 (test) | 340 | 21 | 136 | 63 | 73 | 0 | 0 | 370/370 |
| EP003 (test) | 297 | 17 | 131 | 53 | 78 | 0 | 0 | 317/317 |

- 路由压缩比例稳定：−64% / −60% / −56%；
- **拒绝率 54–60%**：模型确实会提出 `set_time` / `replace_text` / `delete_line`，
  全被 `REVIEW_ACTIONS=keep` 挡下——说明这道闸门不是装饰，否则每集会有 50–80 条
  越权改动落到文件上（EP001 的改写实验已经量过这些改动的方向：更差）；
- 三集上 `applied = 0`，与「无 AI」臂逐条一致。

## 7. 从实验里读出来的三件事

1. **别名少 ≠ 质量高**：干净 CC 上 13 条自动时间修复就已经让 ≤300 ms 从 276 掉到 270；
   这是已知的起点系统性偏早（`eval/protocol.md` §6），不是复核层引入的。
   → 复核层再按 ASR 改，等于把偏差放大。
2. **提示词救不了证据**：模型在 100 条里的判断与人类看 CC 的直觉一致（keep 真实台词），
   但一旦允许它按 `suggested`/`asr_window` 动手，它就忠实地执行了坏证据。
   → 先修证据，再谈自动化改写。
3. **路由才是这一轮的净收益**：275 → 100 条（-64%），且 0 质量变化；
   剩下 142 条在包里就判死为 keep，不必花 token 问。

## 8. 没解决 / 下一步（按收益排序）

1. **给 time route 补音频边界证据**：FireRedVAD/AED 已有 provider（`lib/audio`），
   但只写证据、没有消费者。把每个 cue 的语音区间换成 VAD/AED 边界，
   `time` 路由才可能覆盖到「文本匹配不上但语音清楚」的 cue——这是当前最大缺口。
2. **修对齐的起点偏差**（−0.37/−0.40 s，EP002/03）：这是自动修复和 AI 改写共同的
   天花板；不修，任何按 ASR 改时间的路径都会把字幕整体提前。
3. **planner 的 50 条 `delete_candidate` / 85 条 `replace_text` 大多是噪声**：
   应该在计划层按覆盖率/长度收敛，而不是让复核层否决。这轮实测：
   干净字幕上它们全是真实台词。
4. **拆条/合并**仍未支持（`apply_fixes` 没有 split），包里的 `delete_candidate`
   有一部分其实是「两句挤在一条」。
5. **多集成本**：54 集 × ~100 条 ≈ 5400 次决策。只分类模式下可进一步按 route 采样，
   或先跑 3 集看 keep 率是否稳定。

## 9. 验收口径（进 gate）

- `auto`（L0 自动条数）、`routed`（送模型条数）、`decided/kept/rejected`、`applied` 五项计数；
- L3「人工残留」= `kept` 条数，不是待办队列；
- 判定规则：任何一轮复核若 `applied > 0`，必须同时给 `improved/worsened`
  与 CC 指标（§6 表式），否则视为未验证的自动化。
