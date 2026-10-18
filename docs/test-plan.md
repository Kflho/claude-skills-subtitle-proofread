# 测试与覆盖率计划

> 最后更新：2026-10-18
> 目标：**能力靠测试证明，不靠真实项目碰运气。** 真实项目只负责发现问题；每个问题在修之前先落一个最小 fixture test。

## 0. 现状

```text
python -m pytest -q
576 passed in ~5.1s          # 无网络、无模型、无真实项目依赖
coverage: 36.3% (13,570 statements)
```

原先的 3 个既存失败已修：

| 旧测试 | 问题 | 处理 |
|---|---|---|
| `test_op_region_boundary` / `test_ed_region_boundary` | 断言常量 95/120，代码已改为 180（固定窗口只作安全网） | 改成行为测试：OP 区内的 Latin cue 被强制 clean；正片内仍判 garbled |
| `test_writes_srt_and_report` | `MockWhisperResult` 缺 `cues_to_clear` | conftest fixture 补字段 |

### 覆盖率按目录

| 目录 | 语句 | 覆盖 | 说明 |
|---|---:|---:|---|
| `scripts/lib` | 3,308 | **73.7%** | 对齐/ASR 契约、gates、llm、video_scan 较好；whisper_backends 44% |
| `scripts/fix` | 3,190 | 28.4% | align_repair 91%、whisper_pipeline 36%；whisper_fixer/oped 仍低 |
| `scripts/nouns` | 2,422 | 9.5% | noun_checker 39%、apply_map 新增；extractor/aggregate/glossary 为 0 |
| `scripts` 根 | 2,005 | 7.5% | run_all 10%（helpers 已测）、translate_srt/polish/auto_translate 为 0 |
| `scripts/asr` | 574 | 42.5% | worker 37%（模型分支）、CLI 27%/77% |
| `scripts/reuse` | 389 | 10.3% | common 已测；align/apply 为 0 |
| `scripts/apply` | 373 | 35.1% | cue 级操作已测；ASS/main 未测 |
| `scripts/scan` | 372 | 37.6% | 纯函数与 scan_file 已测；VAD/scan_all 未测 |
| `scripts/utils` | 285 | 80.4% | — |
| `scripts/eval` | 339 | 61.9% | asr_eval 47%；bench 84% |
| `scripts/ass` | 313 | 65.5% | 5 个 check 已测；CLI/main 未测 |

### 做得好的部分

- **P2 对齐核心**（`lib/alignment/*`）：90–98%，含重复台词、乱序 cue、零时长、全局偏移。
- **ASR 契约/路由/适配器**（`lib/asr/*`）：75–97%，fake backend 覆盖注册与 fallback。
- **纯工具**：gates 93%、llm 89%、video_scan 96%、apply_map 双替换防护、reuse/common。
- **结构回归**：`alignment_bench` 用合成 ASR 测 mapper，不碰真实项目。

## 1. 测试原则

1. **零外部依赖**：不下载模型、不联网、不读真实项目。CI/日常跑都必须是秒级。
2. **Tier 分层**：
   - Tier 1 纯函数：直接断言返回值，随机数据固定 seed。
   - Tier 2 tempfile/tmp_path：真实文件系统，但不碰外部进程。
   - Tier 3 mock：ffmpeg / whisper-cli / LLM HTTP / Qwen3 worker 只在边界 mock。
3. **一个 seam 一个 mock**：mock `run_ffmpeg`、`call_chat`、`Qwen3WorkerClient.transcribe` 这类边界，不 mock 内部函数实现。
4. **行为优先**：不要断言常量值或内部字段顺序（OP/ED 那两个旧测试就是反例）。
5. **真实项目只产出 fixture**：在真实素材上踩到 bug → 抽最小字幕/JSON 到 `tests/` 或 `tmp_path` 构造。
6. **每个测试 < 100ms**；整体目标 < 10s。

## 2. 能力矩阵

| 能力 | 入口 | 测试 | 覆盖 | 缺口/下一步 |
|---|---|---|---:|---|
| ASR 契约/注册/路由 | `lib/asr/*` | `test_asr_*`, `test_qwen3_client` | 75–97% | worker 的 chunk 分支需要 fake `model.transcribe` |
| Qwen3 worker | `asr/qwen3_worker.py` | `test_qwen3_worker` | 37% | 分块/offset 合并逻辑无模型级测试（已拆 `_fill_zero_durations` 纯函数） |
| 对齐核心 | `lib/alignment/*` | `test_alignment_*`, `test_cue_*` | 90–98% | 基本齐全 |
| 对齐修复 | `fix/align_repair.py` | `test_align_repair`, `test_fix_by_asr` | 91% | realign 的 ffmpeg 错误分支可补 |
| 对齐 CLI | `asr/align_cues.py` | `test_align_cues_cli` | 77% | `--video` 抽音频分支未测（mock ffmpeg） |
| Phase 1 扫描 | `scan/unified_scanner.py` | `test_unified_scanner` | 38% | `scan_all`、VAD、main/CLI 未测 |
| Phase 2 修复 | `fix/fix_orchestrator.py` | `test_fix_orchestrator` | 38% | `fix_by_reference`、review/apply 路径、AI fragments |
| Whisper 管线 | `fix/whisper_*`, `lib/whisper_backends` | `test_whisper_pipeline`, `test_whisper_backends`, `test_whisper_utils` | 36–44% | 剩余：Tier1/Tier2 编排、真实 VAD、faster-whisper 分支 |
| OP/ED | `fix/oped_*`, `ass/ass_repair` | `test_ass_repair`, `test_oped_detect` | 65–75% | `oped_fixer` / `oped_fill` 为 0 |
| Phase 3 专名 | `nouns/*` | `test_noun_checker`, `test_apply_map` | 9.4% | extractor/aggregate/build_glossary/find_suspect 为 0 |
| 翻译 | `translate_srt.py`, `fix/translate_srt.py`, `polish_zh.py` | — | 0% | 用 fake LLM/百度响应补 |
| 批量应用 | `apply/apply_fixes.py` | `test_apply_fixes` | 35% | `main`、ASS 分支、trad→simp |
| ASS 修复 | `ass/ass_repair.py` | `test_ass_repair` | 65% | `main`/CLI 汇总 |
| 参考复用 | `reuse/*` | `test_reuse_common` | 10% | `align` / `apply` 的采纳判定 |
| 总编排 | `run_all.py`, `fix/episode_workflow.py` | `test_run_all_helpers` | 7.5–8% | 用 fake 步骤跑 dry-run 管线 |
| 评测/回归 | `eval/*` | `test_asr_eval`, `test_alignment_bench` | 47–84% | manifest 缺路径分支 |

## 3. 优先补测（P0 → P2）

### P0：Whisper 音频链路（已补一部分）

已完成（`test_whisper_backends.py` + `test_whisper_pipeline.py`）：

- **dispatch/探测**：unknown backend、缺依赖、显式/自动探测、参数传递；
- **whisper.cpp JSON**：两种时间戳格式、缺失 JSON、解析后删除临时文件；
- **修复区**：`build_fix_regions` 的 garbled / missing / clean 覆盖、长段与区间外跳过；
- **匹配**：`match_whisper_to_cues` 的一轮重叠、未覆盖、±3s 二轮、context 文本过滤；
- **聚类**：`_cluster_garbled_cues` 的邻居上下文、`_merge_nearby_segments`、非对白标记。

仍需补：

- `run_tier1` / `run_tier2` 的编排（mock `run_whisper` + `_concat_wavs`）；
- `get_speech_timeline` 的 WebRTC/ffmpeg 两条路径；
- `filter_low_confidence` 的阈值组合（若未覆盖）；
- `faster-whisper` / `openai-whisper` 后端（fake 模块）。

### P0：翻译链路

- `translate_srt.py`：mock `lib.llm.call_chat` 返回 JSON；断言 prompt 含 glossary/上下文、批处理失败重试、输出 SRT 时间轴不变。
- 百度路径：mock `urllib.request.urlopen`；断言 QPS/失败降级。
- `polish_zh.py`：dry-run 不改文件、apply 才写。

### P1：专名链路

- `nouns/extractor.py`：给日文 cue fixture，断言候选与 known-name 过滤。
- `nouns/aggregate.py`：多集候选 → 标准名聚合（纯 dict 输入）。
- `nouns/build_glossary.py`：术语频次 → markdown 表格。
- `find_suspect_nouns.py`：先测纯函数（词典过滤、scope），再 mock jamdict。

### P1：OP/ED 与复用

- `oped_fixer` / `oped_fill`：ASS fixture（两套 style），断言只改歌词行、不碰正片。
- `reuse/align.py` + `reuse/apply.py`：参考/目标双字幕 fixture，断言 R1/R2 采纳与“不采纳总集篇”规则。

### P2：编排

- `run_all.step_*`：把 `_run` mock 成记录调用，断言步骤顺序与 dry-run 不写盘。
- `episode_workflow._run_pipeline`：fake Fixer 返回 FixReport，断言 step 选择与汇总。
- `fix_by_asr` 已覆盖；`fix_by_whisper` 的 `_apply_whisper_result` 仍要补 cues_to_clear/cut 组合。

## 4. 运行方式

```powershell
# 全量（默认）
python -m pytest -q

# 覆盖率（配置见 .coveragerc）
python -m pytest -q --cov --cov-report=term-missing

# 单模块
python -m pytest -q tests/test_cue_mapper.py
python -m pytest -q tests/test_fix_by_asr.py -k offset

# 只跑 P2 相关
python -m pytest -q tests/test_alignment_base.py tests/test_alignment_timeline.py `
  tests/test_cue_mapper.py tests/test_cue_diagnostics.py `
  tests/test_align_repair.py tests/test_alignment_aligner.py `
  tests/test_align_cues_cli.py tests/test_alignment_bench.py tests/test_fix_by_asr.py
```

## 5. 新增测试检查清单

- [ ] 不需要网络 / API key / 模型 / 真实项目
- [ ] 用 `tmp_path`，不写仓库路径
- [ ] 外部进程、HTTP、worker 在边界 mock
- [ ] 断言行为（状态/文本/时间码），不断言私有实现
- [ ] 失败信息能指出哪条 cue / 哪个时间码
- [ ] 单测 < 100ms；整套 < 10s

## 6. 覆盖率门槛（渐进）

当前 36.3%。不建议一次定 80%，按缺口补：

| 阶段 | 目标 | 重点 |
|---|---:|---|
| 现在 | 36% | 基线 |
| 下一轮 | ≥45% | Whisper 链路 + 翻译 mock |
| 再下一轮 | ≥60% | 专名链路 + OP/ED + 编排 |

> 覆盖率不是目的：**关键路径（扫描→修复→专名→翻译→应用→交付）每个分支至少有一条测试**，比总百分比更重要。
