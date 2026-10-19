# 字幕校对 Skill — 迭代记录

> 每次校对结束后，经人工审核确认后追加一条。
> 此文件独立于 SKILL.md，仅在需要回顾历史时读取，不占用 skill 加载上下文。
>
> **记录规则**：只记架构/流程/接口级变更（新增/删除/重命名脚本、工作流顺序调整、报告格式变更）。不记修 bug、调参数、加 flag 等正常开发活动。

## 2026-10-19 — 专名变体归一 + 译后闸门

### 架构变更
- 新增 `lib/glossary.py`：扁平 ja→zh 词表 + `_meta.aliases` 变体表（人工决策）
  → 安全检查（丢弃等于规范名、单字、规范名子串的变体）→ 最长匹配归一；
  `discover_variants` 用「术语出现/不出现」两侧对比打分出候选，只读不写。
- 新增 `nouns/normalize_variants.py`：`--dry-run`（默认）/`--apply`（`.bak`）/`--discover`。
- 新增 `eval/text_gate.py`：kana/echo/变体/CPS 计数，超阈值退出 1（可接 CI）。
- 文档：`docs/glossary-variants.md`；SKILL.md 加命令；README 能力项。

### 行为修正（接口级）
- 词表拆成扁平映射 + 元数据两份。`_meta` 混在扁平表里会让
  `find_suspect_nouns._load_known_terms` 抛 `unhashable type: 'dict'`。
- 别名必须能证明不被误用：第一版把 `圣诞` 当 `三太` 变体，
  结果「圣诞老人」→「三太老人」；已移出并加回归测试
  `test_project_aliases_do_not_overreach`。

### 证据
- EP051（15 次专名出现、15 批）：无词表 2/15 规范、7 种写法；带词表 **15/15**、0 混批；
  QA 违规 24 → 17，残留假名 5 → 0，回显 1 → 0。
- 52 集副本：归一 174 cue / 22 文件；变体 197 → **0**，kana 83 → 46，
  剩 46 条是 ASR 噪声（属 translation 复核类，不扩别名）。

### 测试
- 1472 → **1511 passed**（+39）：变体表安全过滤/最长匹配/普通词不动/发现器打分、
  CLI dry-run→apply→幂等、四种闸门与阈值、真实词表不越界。

## 2026-10-19 — 复核缺口 AI 填充（管线内，默认只分类）

### 架构变更
- 新增 `scripts/lib/review/`：`pack.py`（缺口 + 证据 + route）、`fill.py`（批量问 LLM +
  动作白名单 + 数值校验）、`apply.py`（逐条应用 + 邻接/空行/重复校验 + 回滚）、
  `stage.py`（fill → apply → 审计日志 → 报告）。
- 产出文件重设计：`temp/scans/<EP>.ai_pack.json`（schema v2）取代
  `<EP>.asr_review.json`；新增 `<EP>.ai_applied.json`；报告只记真实改动。
- 删除 `scripts/apply/asr_review_to_fixes.py`（转换接口）与 `build_asr_review_worklist`；
  `run_all` 在 fix 之后直接跑 `step_review_fill`，`--apply-ai-review` / `--resume` 复用同一步。
- 新增 `REVIEW_ACTIONS`（默认 `keep`）与 `lib/review/pack.route_item` 的证据路由；
  配置文件 `lib/config.py`。

### 行为修正（接口级）
- `fix_by_asr` 的 review 条目不再写报告 ⬜，改为写包；条目只在 `apply` 后进报告。
- 默认不给模型写权：EP001 实测改写 48 条改动 47 条变差（P90 0 → 1692 ms）、
  删条权误删 8–9 条真台词；只分类时 0 应用 0 伤害。

### 证据
- 新增 `scripts/eval/review_fill_eval.py` + `docs/review-ai-fill.md`（EP001 四组实验）；
  ADR-012。路由把 275 条压到 100 条（-64%），质量零变化。

### 测试
- 1469 → **1480 passed**（新增 `test_review_pack/fill/apply/failures`，主流程测试同步改契约）。
- 新增离线全链路测试：QA 判残留 → pack → 假 chat 填充 → apply → 重跑 QA = 0。
- 启发式台账（每个阈值的依据 + 对应测试）见 `docs/review-ai-fill.md` §4。

### 修正（同日，用户指出触发条件错）
- AI 的接入点是**检测器判失败**，不是「ASR 和字幕不一致」。新增
  `lib/review/failures.py`：QA 的 `japanese_residual`/`echo`/`empty`/`glossary_miss`/
  `too_long`/`cps` → `<EP>.zh_pack.json`，默认允许改写为中文。
  实测 EP001–03 各注入 30 条残留 → 28/30/29 条全清、kana 归零、注入集外 0 改动。
- 时间轴移出模型动作：`route=time` 不再发送（实测模型写时间 47/48 变差；
  外部字幕后处理协议同样规定模型不得写时间）。
- 提示词 v2：JSON 模式、≤40 条/≤4000 字批量、ID 缺失补问一次、few-shot、
  `why` 必写依据；翻译相似度 0.716/0.764/0.690 → 0.757/0.765/0.706。

### 避坑
- 主故事窗口的 ASR 缓存是**窗口内时间**（从 0 开始），CC 是集时间码；不补
  `--audio-offset 95` 的话所有 cue 都像「匹配不上」，实验会得到假结论。
- 让模型看「建议值」等于让它执行证据；建议值本身不合格时（整句 → 2 字片段），
  先在包里挡住，别指望提示词纠正。

## 2026-10-18 — 测试收尾 2（whisper_utils / episode_workflow / reuse / eval）

### 架构变更
- 无核心架构变更；新增 `scripts/eval/clean_cc_reference.py`：日文配信 CC → 对白参考
  （去说话人标签、振假名、♪/音效行；不修改原文件）。素材评测的前置步骤。
- 发现并标记（未删除）两处死代码：`episode_workflow._write_ai_review_json`
  无调用方、`--mode` 是已废除的残留 flag。

### 行为修正（接口级）
- `lib/whisper_utils.separate_vocals`：`out_dir = output_dir or dirname(audio) or '.'`；
  原先裸相对文件名会触发 `os.makedirs('')`，在调用 demucs 前崩溃。
- `fix/episode_workflow.step_translate`：成功日志用 `target_lang`，不再硬编码 `→ ja`。
- `fix/episode_workflow.step_diff`：删除计算后未使用的 `fixed_starts`。

### 测试
- 1162 → **1306 passed**；覆盖 **74.0% → 79.0%**；新增 144 例（含 CC 清洁工具 11 例）。
- `whisper_utils` 45→100%、`episode_workflow` 36→100%、`reuse/common` 49→100%、
  `reuse/apply` 65→98%、`asr_eval` 47→99%。
- 矩阵与剩余缺口见 `docs/test-plan.md` §0/§3。

### 避坑
- “默认同输入目录”的函数要写 `dirname(...) or '.'`；Windows/POSIX 下裸文件名都会得到空串。
- `episode_workflow` 的 `--mode` 只被 argparse 接受、不参与选步；不要误以为它能强制 audio/text 模式。

## 2026-10-18 — 测试收尾（run_all / scanner / apply / worker）+ 5 个健壮性修复

### 架构变更
- 无核心架构变更；`lib/`、`fix/`、`nouns/` 的模块边界不变。

### 行为修正（接口级）
- `run_all`：新增 `_load_json_lenient()`；批量枚举的扫描产物（`*_nouns.json`、
  `ai_fragments_*.json`）单个损坏时跳过并告警，不再打死整轮 Phase 1/2/3。
  `lib.project_utils.load_json` 保持严格语义，宽松只放在调用点。
- `apply/apply_fixes` ASS `replace_name`：重建行前 `pop('_raw_parts')`，
  Name 修改才不会被 builder 静默丢弃。
- `apply/apply_fixes._run_trad_to_simp`：SRT 走整行 `translate(TRAD_TO_SIMP_MAP)`；
  原先只走 ASS 的 `split(',', 9)` 路径，SRT 项目繁→简永不生效。
- `asr/qwen3_worker._segments_from_asr_text`：单句场景沿用 ASR 原文，
  保留句内标点/空格（token 拼接会丢 `お前、何してんだよ！` 的顿号）。
- `asr/qwen3_worker.main`：JSON 合法但非对象时回 `ok:false`，
  长驻 worker 不再被一行坏消息打死。

### 测试
- 967 → **1162 passed**；覆盖 **64.0% → 74.0%**；新增 195 例。
- `run_all` 34→96%、`scan` 38→95%、`apply` 35→100%、`worker` 37→99%、
  `qwen3_transcribe` 27→99%、`ass_repair` 66→86%、`reuse/align` 70→96%。
- 新文件 `tests/test_run_all_step_functions.py`（52 项）；6 个测试文件扩展。
- 矩阵与剩余缺口见 `docs/test-plan.md` §0/§3。

### 避坑
- `load_json` 的严格语义不能全局放宽：已有测试锁定「坏 JSON 抛异常」。
  需要容错的地方只包在「批量枚举产物」这类调用点。
- Qwen3-ASR 的句内标点/空格不在 aligner token 里；单段时直接用原文，
  多段才按 token 分配，否则 SRT 会丢标点。

## 2026-10-18 — 测试 P0 链路补齐 + 三处解析/写盘修正

### 架构变更
- 无核心架构变更；`lib/`、`fix/`、`nouns/` 的模块边界不变。
- 新增 `lib.project_utils.display_path()`：日志用相对路径，跨盘时退回原路径
  （原 `os.path.relpath` 在 Windows 跨盘会抛 `ValueError`，一条日志能打死流水线）。

### 行为修正（接口级）
- `translate_srt.load_glossary`：先滤空单元格再取列，表头不再被当成
  「日语→中文」映射；只有第二列含汉字才算译名（`build_glossary` 的
  「日语|次数|变体」表不再产生映射）。
- `fix/oped_fill._apply_templates`：`write_subtitles(path, cues)` 参数顺序修正
  （此前歌词模板永远写不回文件）。
- `fix/align_repair`：相邻 cue 联动求解 —— 移动者用提议窗口、静止者用旧窗口做冲突消解；
  密集字幕可一起重排（EP001 +0.5s 回放自动应用 89 → 262，50ms 还原 70 → 247）。
- `reuse/align.pair_up`：复核结果解包错位（双重 zip），所有候选被缓存为 verified；
  修复后 `test_reuse` 锁定「no / 失败不缓存」。（复用前需重跑 align.py）
- `nouns/noun_checker`：中文呼称正则补全角逗号（`喂，阿童木！` 之前漏检）。

### 测试
- 576 → 967 passed；覆盖 36.3% → 64.0%；新增 13 个测试文件
  （Whisper 编排/VAD、翻译、专名、OP/ED、编排）。详见 `docs/test-plan.md` §0。

### 避坑
- 不要用 PowerShell `Get-Content | Set-Content` 往返改 UTF-8 源文件：中文会被写成
  mojibake（本轮误伤过 `tests/test_find_suspect_nouns.py`，只能整文件重写恢复）。
  编辑中文文件用编辑器/写入工具，并确认 UTF-8。

## 2026-10-18 — 文档分层 + ASR 稳定核心/插件架构

### 架构变更
- ASR 层改为 **稳定核心 + 插件适配器**：`lib/asr/base.py`（契约）、
  `registry.py`（自动发现）、`router.py`（路由）；新模型只加
  `lib/asr/adapters/<name>.py`，不改 router/CLI/评测。
- 默认行为不变：`ASR_ENGINE=whisper`；Qwen3-ASR / Qwen3-FA 通过
  `--engine qwen3` 显式启用。详见 `docs/adr/006-stable-asr-core.md`。
- 文档分层：
  - `SKILL.md`：只放运行时操作（711 → 487 行）
  - `references/`：运行时参考；删除 `references/architecture.md`
  - `dev/`：实现/根因/算法（本地，多数被 .gitignore）
  - `docs/`：ADR/技术路线/进度（git 跟踪）

### 新增
- `scripts/lib/asr/` 稳定核心 + `adapters/qwen3.py`、`qwen3_aligner.py`、`whisper.py`
- `scripts/asr/qwen3_worker.py`、`qwen3_transcribe.py`、`setup_qwen3.ps1`
- `scripts/eval/asr_eval.py`、`eval/`
- `dev/noun-extraction.md`、`translation-pipeline.md`、`troubleshooting.md`
- `docs/tech-route-2026.md`、`docs/tech-route-progress.md`、ADR-005/006

### 合并/删除
- `references/architecture.md` → `dev/technical-details.md` §1/§15，删除原文件
- SKILL.md 的专名实现 → `dev/noun-extraction.md`
- SKILL.md 的翻译并行/模型细节 → `dev/translation-pipeline.md`
- SKILL.md 的错误根因 → `dev/troubleshooting.md`
- SKILL.md 的 Whisper 批量/切段/幻觉 → `dev/whisper-pipeline.md`
- SKILL.md 的 gates 理由 → `dev/technical-details.md` §16

### 避坑
- `Path.write_text()` 在 Windows 默认把 `\n` 转 CRLF；改 SKILL.md 后要
  显式写回 LF，否则 `git diff --check` 全文件 trailing whitespace。
- `dev/` 被 `.gitignore` 忽略；核心实现文档已用 `git add -f` 纳入跟踪，
  以后新增开发文档仍需 `-f`。

## 2026-07-20 — Whisper 集成 + 架构重构

### 架构变更
- SKILL.md 精简到 ~180 行，可选步骤拆为 4 个独立 md（setup-guide / full-mode / ai-asr-fix / whisper-stage）
- 报告改为 `reports/` 下两份持续更新文件（proper-nouns.md + whisper-pending.md）
- 启动流程增加路径确认清单

### 新增脚本
- `whisper_transcribe.py`：调用 whisper.cpp CLI 重转录乱码段
- `issue_tracker.py`：扫描 SRT 生成每集问题清单，自动匹配视频

### 修改脚本
- `generate_romaji_fixes.py`：支持 `--stdin` 管道输入

---

### 2026-07-19 — 多语言残留审查 + 繁体→简体（魔法使莎莉）

**发现**：
- Name字段俄语残留：2011 处，164 个唯一俄语名称（如 `Мама Осаму`、`Мужик`、`Контролёр`）
- 繁体字混入简体文本：3358 处（如 `對不起`、`沒關係`、`這裡`）
- 文本中残留西里尔字母：58 处（如 `А!`、`и`、`папа`），其中 7 处在对话文本、48 处在特效层
- 英语/日语假名残留：149+30 处（如 `Tonkichi`、`And so`、`Fi`）

**改进**：
- 新增 `trad_to_simp_detect.py` — 繁体→简体检测与自动转换脚本（200+ 字符映射表，支持 `--auto` 模式）
- 增强 `source_char_detect.py` — 完全重写为多语言版（支持 `--langs en,jp,ru`，内建 4 种语言预设，支持 `--mode text|names|all`）
- 增强 `names_detect.py` — 新增 `--lang-check` 模式，自动按语言分类 Name 值，标记非目标语言名称
- 新增错误#14（繁体中文残留）和错误#15（Name字段多语言审查）
- 独立/完整模式工作流均新增繁体转换步骤和多语言残留检测步骤
- `script-templates.md` 新增模板 #14，更新模板 #3、#12

**避坑**：
- 西里尔字母 `А`（U+0410）与拉丁字母 `A`（U+0041）外观完全相同但编码不同，需用 Cyrillic 正则而非 Latin 正则检测
- 繁简同形字（如 `真`、`黑`、`只`）不能加入映射表，否则误报
- Name 字段替换必须在 `apply_fixes.py` 的全局替换中加逗号限定（`,Name,`），避免替换到文本内容
- 特效层（Episode/Display/Title）中的西里尔字母可能是动画/卡拉OK 效果的一部分，不应批量替换
