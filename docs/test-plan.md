# 测试与覆盖率计划

> 最后更新：2026-10-18（第二轮：P0 音频/翻译/专名/OP-ED/编排链路补齐）
> 目标不变：**能力靠测试证明，不靠真实项目碰运气。** 真实项目只负责发现问题；每个问题在修之前先落一个最小 fixture test。

## 0. 现状

```text
python -m pytest -q
967 passed in ~6.5s          # 无网络、无模型、无真实项目依赖
coverage: 64.0% (13,607 statements)
```

本轮 **+391 tests**（576 → 967），覆盖率 **36.3% → 64.0%**，0 failed。

新增/扩展测试文件：

| 文件 | 覆盖对象 |
|---|---|
| `test_whisper_pipeline.py`（扩展） | VAD 时间线、Tier 1 合并音频映射、Tier 2、`align_and_fix`、`main()` 的 VAD 闸门/分级/--dry-run |
| `test_whisper_backends.py`（扩展） | faster-whisper / openai-whisper 后端（fake 模块）、错误/缺依赖分支 |
| `test_align_repair.py`（扩展） | 相邻 cue 联动求解（7 项）：密集同时移动 / 冲突提议双方 review / 静止邻居仍拦截 |
| `test_fragment_processor.py`（扩展） | apply_ai_fragments：文本/删除/配对/VAD 对齐、未填条目自动删与 [???] 升级、Whisper 上下文 |
| `test_translate_srt.py` | 语言检测、prompt 组装、glossary/mappings、OP/ED 歌词判定、LLM JSON 容错、回显检测、批量重试、目录过滤、CLI |
| `test_baidu_translate.py` | 百度签名/QPS 参数、错误码/HTTP 错误、语言检测、resume、失败保留原文 |
| `test_polish_zh.py` | 批量 prompt 窗口、JSON 解析、dry-run 不写盘、单条批次跳过、成本估算 |
| `test_nouns_aggregate.py` | 机械合并、LLM 决策应用、running-map 规范化、seed、sidecar 集号 |
| `test_build_glossary.py` | 片假名变体分组、常用词/jamdict 过滤、AI 名词合并、旧表保留、markdown/JSON 输出、CLI |
| `test_nouns_extractor.py` | 时间戳/索引对齐、LLM 响应规范化、同实体合并、集文件解析、sidecar 健康度、`--status` |
| `test_find_suspect_nouns.py` | 已知词加载、人名上下文规则、读音归一化、n-gram 兜底、同源分组合并、端到端检测/聚类 |
| `test_oped_fixer.py` | 噪声/有效文本判定、语种检测、时间聚类的器乐/歌词/正片三分类、自动清理、AI review/apply、`run()` |
| `test_oped_fill.py` | 边界检测（skip/API）、器乐/人声分类、歌词模板匹配（区间/±3s）、只填空行不碰正片、dry-run |
| `test_run_all_steps.py` | `main()` 步骤顺序、`--resume`、`--dry-run`、`--apply-ai-review`、ASR 路由跳过 clean 检查、失败继续 |
| `test_episode_workflow.py` | 资源驱动步骤选择、findings 注入、dry-run、`step_audio` 分派 Whisper/ASR、`step_apply` 只报不写 |
| `test_whisper_fixer.py` | VAD 缓存 L1 闸门、Tier1 三分类、Tier2 升级、重试模型、ja→zh 译文替换 |
| `test_reuse.py` | judge R1–R5、复核 JSON 解析、锚点单调链、pair_up 缓存/失败、apply dry-run/写盘 |
| `test_noun_checker.py` / `test_apply_map.py`（扩展） | 专名检查/统一的提取模式、表格解析、批量替换、清单与映射导出 |

### 覆盖率按目录（本轮 vs 上轮）

| 目录 | 语句 | 覆盖 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `scripts/lib` | 3,313 | **75.3%** | 73.7% | 对齐/ASR 契约、gates、llm、video_scan 较稳；whisper_backends 59% |
| `scripts/fix` | 3,220 | **72.6%** | 28.4% | whisper_pipeline 81%、whisper_fixer 93%、fragment_processor 90%、align_repair 92.5%、oped 70–77% |
| `scripts/nouns` | 2,424 | **67.2%** | 9.5% | aggregate 86%、build_glossary 92%、extractor 85%、find_suspect_nouns 74%、noun_checker 39→79%、apply_map 28→96% |
| `scripts` 根 | 2,005 | **42.3%** | 7.5% | translate_srt 85%、polish_zh 99%、run_all 34% |
| `scripts/asr` | 574 | 42.5% | 42.5% | worker 37%（模型分支）、CLI 27%/78% |
| `scripts/reuse` | 389 | **62.2%** | 10.3% | align 70%、apply 65%、common 46% |
| `scripts/scan` | 372 | 37.6% | 37.6% | 纯函数与 scan_file 已测；VAD/scan_all 未测 |
| `scripts/apply` | 373 | 35.1% | 35.1% | cue 级操作已测；ASS/main 未测 |
| `scripts/eval` | 339 | 61.9% | 61.9% | asr_eval 47%；bench 84% |
| `scripts/ass` | 313 | 65.5% | 65.5% | 5 个 check 已测；CLI/main 未测 |
| `scripts/utils` | 285 | 80.4% | 80.4% | — |

### 做得好的部分

- **P2 对齐核心**（`lib/alignment/*`）：90–98%，含重复台词、乱序 cue、零时长、全局偏移。
- **ASR 契约/路由/适配器**（`lib/asr/*`）：75–97%，fake backend 覆盖注册与 fallback。
- **三条主链路**：Whisper 修复（VAD→Tier1→Tier2）、翻译（LLM/百度）、专名（提取→聚合→glossary）；OP/ED 的器乐/歌词/正片判定。
- **编排行为**：`run_all.main()` 的步骤顺序/`--resume`/`--dry-run`/`--apply-ai-review`，`_run_pipeline` 的资源驱动步骤选择与 VAD 闸门。
- **安全策略**：dry-run 不写盘、VAD 删条默认不写、OP/ED 只覆写歌词、只填空行——每条都有对应断言。

## 1. 测试原则

1. **零外部依赖**：不下载模型、不联网、不读真实项目。CI/日常跑都必须是秒级。
2. **Tier 分层**：
   - Tier 1 纯函数：直接断言返回值，随机数据固定 seed。
   - Tier 2 tempfile/tmp_path：真实文件系统，但不碰外部进程。
   - Tier 3 mock：ffmpeg / whisper-cli / LLM HTTP / Qwen3 worker / janome / jieba / jamdict 只在边界 mock。
3. **一个 seam 一个 mock**：mock `run_whisper`、`call_chat`、`urlopen`、`Fixer`、`_run_pipeline` 这类边界，不 mock 内部函数实现。
4. **行为优先**：不要断言常量值或内部字段顺序（OP/ED 那两个旧测试就是反例）。
5. **真实项目只产出 fixture**：在真实素材上踩到 bug → 抽最小字幕/JSON 到 `tests/` 或 `tmp_path` 构造。
6. **每个测试 < 100ms**；整体目标 < 10s（当前 ~6.3s）。
7. **文件编码**：源文件一律 UTF-8；不要用 PowerShell `Get-Content | Set-Content` 往返改测试文件（会把中文写成 mojibake），用编辑器/写入工具。

## 2. 能力矩阵

| 能力 | 入口 | 测试 | 覆盖 | 缺口/下一步 |
|---|---|---|---|---:|
| ASR 契约/注册/路由 | `lib/asr/*` | `test_asr_*`, `test_qwen3_client` | 75–97% | worker 的 chunk 分支需要 fake `model.transcribe` |
| Qwen3 worker | `asr/qwen3_worker.py` | `test_qwen3_worker` | 37% | 分块/offset 合并逻辑无模型级测试（已拆 `_fill_zero_durations` 纯函数） |
| 对齐核心 | `lib/alignment/*` | `test_alignment_*`, `test_cue_*` | 90–98% | 基本齐全 |
| 对齐修复 | `fix/align_repair.py` | `test_align_repair`, `test_fix_by_asr` | **92.5%** | 相邻联动求解已测；realign 的 ffmpeg 错误分支可补 |
| 对齐 CLI | `asr/align_cues.py` | `test_align_cues_cli` | 78% | `--video` 抽音频分支未测（mock ffmpeg） |
| Phase 1 扫描 | `scan/unified_scanner.py` | `test_unified_scanner` | 38% | `scan_all`、VAD、main/CLI 未测 |
| Phase 2 修复编排 | `fix/whisper_pipeline.py` | `test_whisper_pipeline` | **81%** | VAD/Tier1/Tier2/main 已测；`_cluster_garbled_cues` 回退路径可补 |
| Whisper 后端 | `lib/whisper_backends.py` | `test_whisper_backends` | **59%** | faster/openai 已测；`validate_backend`、缺失参数的 branch 未测 |
| Fixer/片段 | `fix/whisper_fixer.py`, `fragment_processor.py` | `test_whisper_fixer`, `test_fragment_processor` | **90–93%** | 剩余：paired `__DELETE__` 分支、报告更新异常路径 |
| OP/ED | `fix/oped_fixer.py`, `oped_fill.py`, `ass/ass_repair.py` | `test_oped_fixer`, `test_oped_fill`, `test_ass_repair` | **70–77%** | 器乐/歌词/正片分类与填空已测；Whisper 采样与 API 边界仍需 fake |
| Phase 3 专名 | `nouns/*` | 5 个测试文件 | **74–96%** | run_all step_* / scan / apply 的主流程仍缺 |
| 翻译 | `translate_srt.py`, `fix/translate_srt.py`, `polish_zh.py` | 3 个新测试文件 | **85–99%** | 只剩 `main` 的少量分支与真实 API 形状 |
| 批量应用 | `apply/apply_fixes.py` | `test_apply_fixes` | 35% | `main`、ASS 分支、trad→simp |
| 参考复用 | `reuse/*` | `test_reuse`, `test_reuse_common` | **62%** | align CLI 主流程未测 |
| 总编排 | `run_all.py`, `fix/episode_workflow.py` | `test_run_all_*`, `test_episode_workflow` | 34–36% | 步骤序列/闸门已测；各 `step_*` 的实际副作用仍缺 |
| 评测/回归 | `eval/*` | `test_asr_eval`, `test_alignment_bench` | 47–84% | manifest 缺路径分支 |

## 3. 优先补测（P0 → P2）

### P0（本轮已完成）

- [x] `run_tier1` / `run_tier2` 编排（mock `run_whisper` + 真实 WAV 拼接）；
- [x] `get_speech_timeline` 的 WebRTC / silencedetect 两条路径（fake webrtcvad + 真实 16k WAV）；
- [x] `faster-whisper` / `openai-whisper` 后端（fake 模块，断言解码参数与元数据映射）；
- [x] 翻译链路：prompt 含 glossary/上下文、JSON 容错、回显重试、时间轴不变、dry-run 不写盘；
- [x] 百度路径：签名/QPS/错误降级/resume；
- [x] 专名链路：extractor / aggregate / build_glossary / find_suspect_nouns 的纯函数与边界；
- [x] OP/ED：ASS/SRT 双 style 下只改歌词、只填空行、不碰正片；
- [x] 编排：`run_all.main` 顺序与 fast-path、`_run_pipeline` 的资源驱动步骤、`step_audio` 的 Whisper/ASR 分派。
- [x] 相邻 cue 联动求解（`_resolve_joint_time_fixes`）：EP001 +0.5s 回放自动应用 89 → 262、50ms 还原 70 → 247；`fix_by_asr` 端到端测试锁定。

### P1（本轮已完成大半）

- [x] `fix/whisper_fixer.py`（93%）与 `fix/fragment_processor.py`（90%）：VAD 缓存闸门、Tier1 三分类、Tier2/重试、AI 片段生成与应用。
- [x] `nouns/find_suspect_nouns.py`（74%）的 `_cluster_by_source` / `search_corrections` / `_apply_correction_fixes` / `_format_candidates`。
- [x] `reuse/align.py`（70%）、`reuse/apply.py`（65%）：R1–R5 采纳规则、复核缓存与失败、锚点链、dry-run/写盘。
- [x] `nouns/noun_checker.py`（79%）与 `nouns/apply_map.py`（96%）的主流程：提取模式、表格解析、运行替换、清单与映射导出。
- [ ] `run_all.step_scan/step_nouns/step_apply_all/step_deliver/step_clean` 的副作用分支（只测了 main 的调用顺序）。

### P2

- `scan/unified_scanner.py` 的 `scan_all` / VAD / main。
- `apply/apply_fixes.py` 的 main、ASS 分支、繁→简。
- `qwen3_worker` 的 chunk 分支（fake `model.transcribe`）、`qwen3_transcribe` 的 CLI 选项。
- `whisper_utils` 的音频/VAD 段（45%，大量 ffmpeg 包装）。

## 4. 运行方式

```powershell
# 全量（默认）
python -m pytest -q

# 覆盖率（配置见 .coveragerc）
python -m pytest -q --cov --cov-report=term-missing

# 单模块
python -m pytest -q tests/test_cue_mapper.py
python -m pytest -q tests/test_fix_by_asr.py -k offset
python -m pytest -q tests/test_oped_fixer.py -k lyric

# 本轮新增链路
python -m pytest -q tests\test_whisper_pipeline.py tests\test_whisper_backends.py `
  tests\test_translate_srt.py tests\test_baidu_translate.py tests\test_polish_zh.py `
  tests\test_nouns_aggregate.py tests\test_build_glossary.py `
  tests\test_nouns_extractor.py tests\test_find_suspect_nouns.py `
  tests\test_oped_fixer.py tests\test_oped_fill.py `
  tests\test_run_all_steps.py tests\test_episode_workflow.py

# 只跑 P2 相关
python -m pytest -q tests/test_alignment_base.py tests/test_alignment_timeline.py `
  tests/test_cue_mapper.py tests/test_cue_diagnostics.py `
  tests/test_align_repair.py tests/test_alignment_aligner.py `
  tests/test_align_cues_cli.py tests/test_alignment_bench.py tests/test_fix_by_asr.py
```

## 5. 新增测试检查清单

- [ ] 不需要网络 / API key / 模型 / 真实项目
- [ ] 用 `tmp_path`，不写仓库路径
- [ ] 外部进程、HTTP、worker、分词器（janome/jieba/jamdict）在边界 mock
- [ ] 断言行为（状态/文本/时间码），不断言私有实现
- [ ] 失败信息能指出哪条 cue / 哪个时间码
- [ ] 单测 < 100ms；整套 < 10s
- [ ] 文件保持 UTF-8（不要 PowerShell 文本往返）

## 6. 覆盖率门槛（渐进）

当前 **64.0%**。不追求一次到 80%，按缺口补：

| 阶段 | 目标 | 重点 |
|---|---:|---|
| 上轮 | 36% | 基线 |
| **现在** | **64.0%** | P0 链路 + 相邻联动 + 修复/片段/复用/专名补齐 |
| 下一轮 | ≥70% | scan / apply 主流程、run_all 各 step、worker chunk |
| 再下一轮 | ≥75% | worker chunk、scan VAD、CLI 主流程 |

> 覆盖率不是目的：**关键路径（扫描→修复→专名→翻译→应用→交付）每个分支至少有一条测试**，比总百分比更重要。

## 7. 本轮顺带修掉的真 bug（都有回归测试）

| 位置 | 问题 | 测试 |
|---|---|---|
| `translate_srt.load_glossary` | 表头被当成一条「日语→中文」映射；`build_glossary` 的「日语\|次数\|变体」表被当译名表，数字会被预替换进正文 | `test_load_glossary_skips_header_but_keeps_mapping`、`test_load_glossary_ignores_count_tables` |
| `nouns/aggregate.py`、`extractor.py`、`translate_srt.py` | `os.path.relpath` 在 Windows 跨盘（temp 在 C:、项目在 D:）抛 `ValueError`，日志行能打死整条流水线 | `test_main_init_seed_and_merge_sidecar` 等（改用 `lib.project_utils.display_path`） |
| `fix/oped_fill._apply_templates` | `write_subtitles(cues, fpath)` 参数顺序反了，歌词模板永远写不回文件 | `test_apply_templates_fills_only_blank_op_ed_cues` |
| `reuse/align.pair_up` | 双重 `zip` 让 work 返回值多包一层，`r` 恒为批次列表 | 所有候选一律缓存 verified（LLM 复核失效） | `test_pair_up_aborts_when_all_checks_fail`、`test_pair_up_reuses_cache_without_llm` |
| `nouns/noun_checker` 中文呼称 | 正则不识别全角逗号（`喂，阿童木！`） | 呼称候选漏检 | `test_chinese_extract_suffix_calling_intro_quoted` |
