# 复核环节的 AI 接入：触发条件、启发式台账、提示词与实验（2026-10）

> 旧「人工复核方案」= 报告/checklist 填「修正:」→ 脚本回写。ADR-003 把报告降级成
> 只读导出后断掉。现在按同一思路在管线内重建：**检测器判失败的条目**进包，
> AI 填、校验、应用，全程无人工队列、无新 CLI。
>
> 复现命令见 §7；数据在 `temp/eval/*`（gitignored）。

## 0. 结论先行

1. **AI 的接入点是「自动流程判失败」，不是「ASR 和字幕不一致」。**
   自动流程能判的（乱码分类、QA 规则、repair）它已经做了；剩下的才是 AI 的活：
   - `kind=translation`：QA 判 `japanese_residual` / `echo` / `empty` /
     `glossary_miss` / `too_long` / `cps`，自动 repair 没清掉的；
   - `kind=asr_repair`：对齐后仍无可信判据的文本/删条候选（按时段证据路由）。
2. **translation 类实测有效**：EP001/02/03 各注入 30 条日语残留 →
   AI 清掉 28/30/29 条（**全清**），kana 166/315/237 → **0**，
   与人工译文的字符相似度 0.71–0.77，注入集外改动 **0** 条。
3. **ASR 分歧类不该让模型写**：给 `set_time`/`replace_text`/`delete` 权限时，
   v1 提示词在干净字幕上 48 条改动 **47 条变差**（P90 0 → 1692 ms）。
   因此时间轴不进模型动作（`route=time` 不发送），其余动作默认只分类；
   v2 下即便显式给权，EP001 也是 **0 应用、0 改动**。
4. **旧工作单的建议值是噪声**：75 条 `new_text` 建议里 **51 条不到原文 40%**
   （整句 → `おい`/`は`/`まで`），照做就是毁台词。
5. 净收益：translation 失败清空、ASR 侧零误伤；送模型的条数从 275 降到 60（EP001）。

## 1. 断点与文件重设计

| 旧件 | 现状 |
|---|---|
| 报告里填「修正:」 | ADR-003：报告是 JSON 的只读导出，填了没人读 |
| `apply_review_fixes.py` / `ai_review_batch.py` | 已删除 |
| `<EP>.asr_review.json` + `asr_review_to_fixes.py` | 本次删除 |

新产出：

| 文件 | 内容 |
|---|---|
| `temp/scans/<EP>.ai_pack.json` | ASR 缺口（证据 + route + 决定） |
| `temp/scans/<EP>.zh_pack.json` | QA 失败条目（源句 + 现译文 + failure + 决定） |
| `temp/scans/<EP>.{ai,zh}_applied.json` | 逐条结果：applied / rejected / kept + 原因 |
| `reports/问题解决报告.md` | 只记真实改动（✅/🗑️），keep 不进报告 |

## 2. 流程

```
fix_by_asr ──► <EP>.ai_pack.json（route=time/text/delete/keep）
translate + QA(.qa.json) ──► <EP>.zh_pack.json（failure 列表）
        └─ review 阶段（run_all 内，紧跟 fix/translate，无 CLI）
             ├─ 只发 route ∈ {text, delete} 与 kind=translation 的条目
             ├─ 批量：≤40 条 / ≤4000 字符；JSON 模式；ID 缺漏补问 1 次
             ├─ 动作白名单（route × kind 策略）→ 数值/语言校验
             └─ 应用 + 逐条回滚（邻行重叠/空行/重复/最后一条）
```

## 3. 谁会被发送（路由）

| route | 条件（`lib/review/pack.route_item`） | 发送 | 允许动作 |
|---|---|---|---|
| `time` | 匹配段 `0.15 < |start_error| ≤ 2.0 s` 且 `similarity ≥ 0.5`；或窗口语音位移 0.15–2.5 s 且 `local_similarity ≥ 0.55` | **否**（L0 的时间轴证据） | — |
| `text` | `similarity ≥ 0.5` 且长度比 0.4–2.2；或 `local_similarity ≥ 0.75` | 是 | keep / replace_text |
| `delete` | 删条候选且原文 ≤ 6 字 | 是 | keep / delete_line |
| `keep` | 其余（ASR 不一致但无可比证据） | 否 | — |

translation 条目一律 `route=text`（`glossary_miss`/`too_long` 等同理），
但语言方向相反：replacement 为中文、不得含假名。

## 4. 启发式台账（AI 介入相关）

> 原则：每条启发式都要能说出「凭什么」和「谁测的」。没有证据的常数不许进默认路径。

### 4.1 路由与打包（`pack.py` / `failures.py`）

| 位置 | 规则 | 凭什么 | 测试 |
|---|---|---|---|
| `ROUTE_MIN_SIMILARITY=0.50` | 文本匹配低于此不进 text 路由 | EP001 干净字幕：similarity < 0.5 的条目 0 条被模型改对；ASR 覆盖率 42–45%，更低就是噪声 | `test_route_item_thresholds` |
| `ROUTE_MAX_TIME_ERROR_S=2.0` / `MIN=0.15` | 时间证据窗口 | 协议 §6：>2 s 的「误差」多是错误匹配；<0.15 s 不值得动 | 同上 |
| `ROUTE_MIN/MAX_LENGTH_RATIO=0.4/2.2` | 替换必须长度可比 | 旧包 51/75 建议 <40% 长度（整句→2 字） | 同上 |
| `ROUTE_DELETE_MAX_CHARS=6` | 只有 ≤6 字才算噪声候选 | 干净字幕上删条候选 91 条全是真台词；长度是唯一廉价护栏 | 同上 |
| `ROUTE_MAX_WINDOW_SHIFT_S=2.5` | 窗口证据的时间位移上限 | 超过就是换了另一句 | `test_window_speech_gives_a_time_proposal_without_a_text_match` |
| `MODEL_ROUTES=('text','delete')` | 时间不进模型 | v1 实测：模型写时间 48 改 47 差；外部协议也规定模型不得写时间 | `test_time_route_is_never_sent_to_the_model` |
| `ASR_WINDOW_PAD_S=1.0` / `MAX_ASR_WINDOW_CHARS=60` | 窗口证据的采样 | 主故事窗口 ASR 是窗口内时间（`--audio-offset 95`）；±1 s 足够覆盖轻微错位 | `test_asr_window_reports_local_speech_only` |
| `CONTEXT_SPAN=2` / `MAX_CONTEXT_CHARS=80` | 上下各 2 条 | 字幕对话 2 条足够判指代；再多是 token 浪费 | `test_build_pack_keeps_context_and_seconds` |
| `TEXT_FIXABLE` 集合 | 哪些 QA reason 可文本修 | `timing` 不是文本问题；其余都是「这一行本身不对」 | `test_classify_reasons` |

### 4.2 决策校验（`fill.py`）

| 位置 | 规则 | 凭什么 | 测试 |
|---|---|---|---|
| `MAX_SHIFT_S=8.0` | 单条位移上限 | 超过 8 s 必是错误匹配（整集偏移另有机理） | `test_model_cannot_move_times`（时间已不发送） |
| `MIN/MAX_DURATION_S=0.3/12.0` | 时长下限/上限 | 字幕可读性下限 0.3 s；12 s 已超过两行字幕的生理上限 | `test_review_apply` |
| 长度 `2.2×+8` / `0.35×`（translation 下限 1 字） | 替换长度护栏 | 旧包碎片灾难；translation 允许单字（`ウン`→`嗯`） | `test_short_translation_may_be_one_character` |
| kana 检查（translation） | 替换不得含假名 | 这一类失败就是残留假名 | `test_translation_rewrite_with_kana_is_rejected` |
| `_has_japanese`（asr_repair） | 替换不得丢日文 | 防止模型顺手翻译 | `test_fill_rejects_unsafe_decisions_and_falls_back_to_keep` |
| `ROUTE_ACTIONS` | route×动作白名单 | 动作越界=证据不支持 | `test_action_outside_the_route_is_rejected` |
| `MAX_BATCH_ITEMS=40` / `MAX_BATCH_CHARS=4000` | 批量上限 | 外部协议同量级（40 条/4000 字）；防单批 token 失控 | `test_batches_respect_the_character_budget` |
| `MAX_RETRIES=1` + 缺 ID 补问 | ID 完整性 | 模型漏条会让其它条静默不动；补问一次比整批重跑便宜 | `test_missing_ids_are_retried_once` |
| `duplicate_id`/`unknown_id` 计数 | 协议违规可观测 | 否则协议错只是「少改几条」 | `test_duplicate_and_unknown_ids_are_counted` |

### 4.3 应用与策略（`apply.py` / `config.py`）

| 位置 | 规则 | 凭什么 | 测试 |
|---|---|---|---|
| `MAX_NEIGHBOUR_OVERLAP_S=0.05` | 与邻行重叠 >50 ms 回滚 | 字幕重叠不可读；50 ms 是显示容差 | `test_set_time_that_overlaps_neighbour_rolls_back` |
| 最后一条不许删 | 防整集清空 | 误删代价不可逆 | `test_delete_line_applied_and_last_cue_protected` |
| `REVIEW_ACTIONS=keep`（默认） | ASR 侧只分类 | 给权实测 47/48 变差、误删 8–9 条真台词 | `test_triage_only_mode_rejects_rewrites_and_deletes` |
| `REVIEW_TRANSLATION_ACTIONS=keep,rewrite`（默认） | translation 可改写 | 30 条注入全清、0 误伤（§6.1） | `test_translation_stage_keeps_its_own_policy` |
| 无 key → 不填充只应用 | 不静默降级 | 与翻译一致：明确失败而不是假装做过 | `step_review_fill` 打印提示 |

## 5. 提示词调研与优化（v1 → v2）

外部参考（[MAW Launcher 字幕后处理协议](https://github.com/Moyf/moys-asr-workflow/blob/v1.5.0-beta.7/docs/LLM_POSTPROCESS_PROTOCOL.md)，
私有工具的公开协议文档）给的几条可直接借鉴：

1. **模型不写时间轴**：时间真源在本地，模型只改文字；
2. **ID 覆盖规则**：每个输入 ID 恰好一次、保序、text 非空；不合规只跳过该组；
3. **批量上限**：≤40 条 / 约 4000 字；
4. **`response_format: json_object` + `temperature 0.1`**；
5. 翻译任务严格一对一，不合并/拆分。

采纳情况与效果：

| 改动 | v1 | v2 | 依据 |
|---|---|---|---|
| 时间不进模型 | `set_time` 可选 | `route=time` 不发送 | 实测 47/48 变差；外部协议 |
| JSON 模式 | 无 | `response_format=json_object` | 协议；减少围栏/散文 |
| 批量 | 固定 15 条 | ≤40 条 / ≤4000 字 | 协议 |
| ID 完整性 | 不检查 | 缺条补问 1 次，重复/未知计数 | 协议 |
| 语言约束 | 只查「不得丢日文」 | translation 查「不得含假名」，长度地板 1 字 | 本实验 `ウン→嗯` 被误拒 |
| 少样本 + 证据字段 | 无 | 2 例 few-shot；`why` 必写依据 | 格式合规、可审计 |
| 动作白名单 | route 限 | route × kind 两套策略 | ASR 与 translation 风险不同 |

A/B（translation 注入 30 条，同一 seed、同一模型、思考关闭）：

| 集 | v1 清零 | v1 相似度 | v2 清零 | v2 相似度 | v2 耗时 |
|---|---:|---:|---:|---:|---:|
| EP001 | 28/28 | 0.716 | 28/28 | **0.757** | 9.5 s |
| EP002 | 30/30 | 0.764 | 30/30 | 0.765 | 10.7 s |
| EP003 | 29/29 | 0.690 | 29/29 | **0.706** | 10.2 s |

ASR 侧（EP001 干净，242 缺口，13 自动）：

| 配置 | 送模型 | 应用 | 改动 | 变好/变差 | P90 |
|---|---:|---:|---:|---|---:|
| v1 + 改写权 | 100 | 33 | 48 | 0 / **47** | 1692 ms |
| v2 + 改写+删条权 | 60 | **0** | 0 | – | 0 ms |
| v2 只分类（默认） | **0（不发送）** | 0 | 0 | – | 0 ms |

> 2026-10-19 追加：**没有写权就不问模型**。`asr_repair` 默认只有 `keep` 权，
> 这类条目现在直接不发送（`skipped_no_rights` 计数），不再花钱让模型复述 keep。
> 只有 `translation`（默认 `keep,rewrite`）会真正产生调用。

## 6. 实验

### 6.1 translation（检测器判失败）

```powershell
python scripts\eval\review_translation_eval.py `
  --zh temp\translation_eval\EP001.full.baseline.srt `
  --ja temp\references\digimon\EP001.main.srt `
  --out-dir temp\eval\p2_EP001 --inject 30 --seed 3
```

把 30 条中文替换成日文原句（同时触发 `japanese_residual` + `echo`），
走真实 QA → pack → fill → apply → 重跑 QA。EP001/02/03：

| 集 | 注入 | QA 条数 | 送模型 | 清掉 | 残留 | kana | 相似度 | 注入外改动 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EP001 | 30 | 28 | 28 | 28 | 0 | 166→0 | 0.757 | 0 |
| EP002 | 30 | 30 | 30 | 30 | 0 | 315→0 | 0.765 | 0 |
| EP003 | 30 | 29 | 29 | 29 | 0 | 237→0 | 0.706 | 0 |

### 6.2 ASR 分歧（不该让模型写）

`review_fill_eval.py`，干净 CC 做金标，只跑自动修 vs 自动修+复核（数字见 §5 表）。

### 6.3 test split（冻结配置，只跑一次）

| 集 | 缺口 | 自动 | 送模型 | 填充 | 拒绝 | 应用 |
|---|---:|---:|---:|---:|---:|---:|
| EP001 (dev) | 242 | 13 | 60 | 60 | 0 | 0 |
| EP002 (test) | 340 | 21 | 44 | 31 | 13 | 0 |
| EP003 (test) | 297 | 17 | 41 | 41 | 0 | 0 |

EP002 的 13 条拒绝来自长度/语言校验（`replacement much shorter/longer`、
`dropped Japanese`），即模型给了不合规替换——闸门按设计挡住。

## 7. 复现与验收

```powershell
$env:PYTHONPATH = "scripts"
# ASR 侧对照（dev）
python scripts\eval\review_fill_eval.py --episode EP001 `
  --cc temp\references\digimon\EP001.main.srt `
  --asr temp\asr\digimon_ep001_main.qwen3.json `
  --out-dir temp\eval\check --degrade-time 0 --degrade-text 0 --limit-items 0
# translation 侧
python scripts\eval\review_translation_eval.py --zh ... --ja ... --inject 30
```

入库测试（不依赖网络）：

| 文件 | 锁什么 |
|---|---|
| `tests/test_review_pack.py` | 路由阈值、窗口证据、建议值不带噪声、stats 口径 |
| `tests/test_review_fill.py` | 路由/策略白名单、语言与长度校验、批量预算、ID 补问、重复/未知 ID、时间不发送、提示词硬规则 |
| `tests/test_review_apply.py` | 逐条应用、重叠/空行/重复回滚、最后一条保护、dry-run |
| `tests/test_review_failures.py` | QA→pack 转换、decision 复用、translation 策略独立、**离线全链路（detector→fill→apply→QA 清零）** |

## 8. 没解决 / 下一步

1. **音频侧证据**：`route=time` 现在只有 ASR 文本匹配；接 FireRedVAD/AED 的语音边界，
   才能覆盖「文本对不上但语音清楚」的 cue（当前 0 条进入模型）。
2. **修对齐起点偏差**（−0.37/−0.40 s）：这是 L0 时间修复和任何时间证据共同的天花板。
3. **planner 的 `delete_candidate`/`replace_text` 噪声**：在计划层按覆盖率收敛，
   而不是让复核层否决（EP001 干净字幕上 30 条 delete 全被模型 keep）。
4. **拆条/合并**：`apply_fixes` 仍无 split，部分 `delete_candidate` 其实是「两句挤一条」。
5. **成本**：54 集 × 40–60 条 ≈ 2500 次决策；只分类模式下可再按 route 采样。
