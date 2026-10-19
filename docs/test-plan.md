# 测试与覆盖率计划

> 最后更新：2026-10-18（第十轮：ASR-first 重设计 + 去百度）
> 目标不变：**能力靠测试证明，不靠真实项目碰运气。** 真实项目只负责发现问题；每个问题在修之前先落一个最小 fixture test。

## 0. 现状

```text
python -m pytest -q
1420 passed in ~9s           # 无网络、无模型、无真实项目依赖
coverage: 79.9% (15,105 statements)
```

本轮 **+43 tests**（1377 → 1420）：ASR review 工作单/`set_time`/AI review 列表、AudioHints 契约与主流程接线、翻译 QA/阶段/repair/chrF/时间轴、review 转换器、全集 manifest、ASR chunk_sec/边界切块、series glossary 验证。
覆盖率 79.7% → 79.8%，0 failed。

新增/扩展测试文件：

| 文件 | 覆盖对象 |
|---|---|
| `test_release_gate.py`（新） | gate 指标提取、P2-14 目标判定（只对 test）、基线回归判定、覆盖率下降、baseline 往返与忽略无 `metrics` 的基线 |
| `test_episode_benchmark.py`（扩展） | signed 起点误差、绝对误差分档、P90 归因（zero/collapsed/stretched/short/low/ambiguous/repeat/plain）、对话文本分解（matched-span CER、字符覆盖） |
| `test_alignment_quality.py`（扩展） | `lost_spans`：塌缩 run 前的时间空洞、overlong token、阈值可配 |
| `test_fix_by_asr.py`（扩展） | 整集偏移默认关闭、显式 `global_offset=True`、`ASR_GLOBAL_OFFSET` 环境默认 |
| `test_asr_registry.py`（扩展） | aligner 适配器尊重 `ASRConfig.model` 本地路径（离线模型不再回落到 Hub） |
| `test_project_utils.py` / `test_subtitle_session.py`（扩展） | 未补零视频名解析（`01、Title.mkv` → EP001）、201/1080p 反例、SubtitleSession 视频解析 |
| `test_whisper_fixer.py`（改造） | 替换翻译改走 LLM：有 key 翻译、无 key 保留原文、失败批次保留原文；删除百度 mock |
| `test_config.py`（改造） | 断言 `BAIDU_*` 配置已不存在（ADR-010） |
| `test_apply_fixes.py` / `test_fix_by_asr.py` / `test_run_all_step_functions.py`（扩展） | ASR review 工作单（`<EP>.asr_review.json`）、`set_time` 改动时间码/秒值/非法区间、AI review 步列出工作单；`audio_hints` 证据写入/失败降级 |
| `test_audio_hints.py`（新） | `AudioEvent`/`AudioHints` 契约往返、summary 比例；FireRedVAD provider 解析 worker JSON、失败/错误 JSON/缺失模型 |
| `test_episode_workflow.py`（扩展） | `--audio-hints` 从 `step_audio` 透传到 `fix_by_asr`（默认关） |
| `test_translation_qa.py`（新） | 日语残留、回显（汉字人名不误判）、glossary 命中、行长/CPS、聚合报告 |
| `test_translation_stage.py`（新） | 源 SRT/glossary 发现、命令契约、成功/失败/缺源、dry-run |
| `test_translation_repair.py`（新） | 只修 QA 标出的条；无违规时不调用 LLM |
| `test_translate_srt.py`（扩展） | 硬约束 prompt、`write_qa_report` 落盘 |
| `test_asr_review_to_fixes.py`（新） | decision→apply_fixes 转换（set_time/replace_text/delete）、校验错误、split 跳过、dedupe、CLI 写盘 |
| `test_build_manifest.py`（新） | 视频/CC 集号映射、dev/test split、已知 main_window、asr_json 命名 |
| `test_qwen3_client.py`（扩展） | `chunk_sec` / `chunk_boundaries` 透传到 worker payload |
| `test_aed_chunk.py`（新） | AED speech gap 选切点；无 gap 回退固定目标 |
| `test_nouns_validate_glossary.py`（新） | 日文/中文同现验证；无证据词条丢弃；集号解析 |
| `test_translation_stage.py`（扩展） | series glossary 发现回退 |

### 覆盖率按目录（本轮 vs 上轮）

| 目录 | 语句 | 覆盖 | 上轮 | 说明 |
|---|---:|---:|---:|---|
| `scripts/lib`（含子目录） | 3,810 | **86.3%** | 85.8% | 翻译 QA/chrF/时间轴；ASR 边界切块；AudioHints 契约/provider |
| `scripts/fix` | 3,216 | **77.5%** | 78.0% | 翻译阶段/repair + AudioHints provider；whisper_pipeline 81% |
| `scripts/nouns` | 2,500 | **67.7%** | 67.2% | series glossary 证据验证；`auto_classify` / `auto_clean_glossary` 仍 0%（旧入口） |
| `scripts` 根 | 2,123 | **66.7%** | 65.7% | run_all 96%、translate_srt 85%、polish_zh 99% |
| `scripts/asr` | 613 | **94.9%** | 95.0% | worker 边界切块/CLI |
| `scripts/reuse` | 389 | **97.9%** | 97.9% | align 96%、apply 98%、common 100% |
| `scripts/scan` | 372 | **94.6%** | 94.6% | scan_all/VAD/CLI 补齐 |
| `scripts/apply` | 504 | **96.4%** | 100.0% | main/ASS/繁→简 + ASR review 转换器 |
| `scripts/eval` | 1,164 | **87.8%** | 90.3% | release gate；benchmark；全集 manifest；AED 切块实验 |
| `scripts/ass` | 313 | **86.3%** | 86.3% | CLI main 已测 |
| `scripts/utils` | 285 | **80.4%** | 80.4% | — |
| **合计** | **15,289** | **79.8%** | 79.9% | — |

### 做得好的部分

- **P2 对齐核心**（`lib/alignment/*`）：90–98%，含重复台词、乱序 cue、零时长、全局偏移。
- **ASR 链路**：契约/路由/适配器 75–97%；worker 99%、CLI 99%；`whisper_utils` 100%（含 VAD/抽音频/ASS 写回）。
- **三条主链路**：Whisper 修复（VAD→Tier1→Tier2）、翻译（LLM/百度）、专名（提取→聚合→glossary）；OP/ED 的器乐/歌词/正片判定。
- **编排与步骤**：`run_all.main()` + 各 `step_*` 96%；`episode_workflow` 100%（含 `_run_pipeline` 资源组合）。
- **批量应用 / 复用**：`apply_fixes` 100%；`reuse/*` 97.9%。
- **评测链路**：`asr_eval` 99%、CC 参考清洁 98%（`clean_cc_reference`）。
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
6. **每个测试 < 100ms**；整体目标 < 10s（当前 ~7.2s）。
7. **文件编码**：源文件一律 UTF-8；不要用 PowerShell `Get-Content | Set-Content` 往返改测试文件（会把中文写成 mojibake），用编辑器/写入工具。

## 2. 能力矩阵

| 能力 | 入口 | 测试 | 覆盖 | 缺口/下一步 |
|---|---|---|---|---:|
| ASR 契约/注册/路由 | `lib/asr/*` | `test_asr_*`, `test_qwen3_client` | 75–97% | 基本齐全 |
| Qwen3 worker | `asr/qwen3_worker.py` | `test_qwen3_worker` | **99%** | chunk/offset/零时长/协议/align 已测；剩 soundfile 兜底 |
| Qwen3 CLI | `asr/qwen3_transcribe.py` | `test_qwen3_cli` | **99%** | — |
| 对齐核心 | `lib/alignment/*` | `test_alignment_*`, `test_cue_*` | 90–98% | 基本齐全 |
| 对齐修复 | `fix/align_repair.py` | `test_align_repair`, `test_fix_by_asr` | **92.5%** | 相邻联动求解已测；realign 的 ffmpeg 错误分支可补 |
| 对齐 CLI | `asr/align_cues.py` | `test_align_cues_cli` | 78% | `--video` 抽音频分支未测（mock ffmpeg） |
| Phase 1 扫描 | `scan/unified_scanner.py` | `test_unified_scanner` | **94.6%** | `scan_all`/VAD/CLI 已测；剩 tokenizer import 与少量分支 |
| Phase 2 修复编排 | `fix/whisper_pipeline.py` | `test_whisper_pipeline` | **81%** | VAD/Tier1/Tier2/main 已测；`_cluster_garbled_cues` 回退路径可补 |
| Whisper 后端 | `lib/whisper_backends.py` | `test_whisper_backends` | **59%** | faster/openai 已测；`validate_backend`、缺失参数的 branch 未测 |
| Whisper 工具 | `lib/whisper_utils.py` | `test_whisper_utils` | **100%** | VAD/ASS/抽音频/人声分离/文本判定全覆盖 |
| Fixer/片段 | `fix/whisper_fixer.py`, `fragment_processor.py` | `test_whisper_fixer`, `test_fragment_processor` | **90–93%** | 剩余：paired `__DELETE__` 分支、报告更新异常路径 |
| OP/ED | `fix/oped_fixer.py`, `oped_fill.py`, `ass/ass_repair.py` | `test_oped_fixer`, `test_oped_fill`, `test_ass_repair` | **70–86%** | `ass_repair` CLI 已补；Whisper 采样与 oped 多文件差异仍可补 |
| Phase 3 专名 | `nouns/*` | 5 个测试文件 | **74–96%** | `auto_classify` / `auto_clean_glossary` 仍 0%（旧入口，不在主链） |
| 翻译 | `translate_srt.py`, `fix/translate_srt.py`, `polish_zh.py` | 3 个测试文件 | **85–99%** | 只剩 `main` 的少量分支与真实 API 形状 |
| 批量应用 | `apply/apply_fixes.py` | `test_apply_fixes` | **100.0%** | 全部分支已测；2 个防御性 partial |
| 参考复用 | `reuse/*` | `test_reuse`, `test_reuse_common` | **97.9%** | align 96%、apply 98%、common 100% |
| 总编排 | `run_all.py`, `fix/episode_workflow.py` | `test_run_all_*`, `test_episode_workflow` | **96% / 100%** | run_all 剩日志/防御分支 |
| 评测/回归 | `eval/*` | `test_asr_eval`, `test_alignment_bench` | **84–99%** | asr_eval 99%（limit/list-engines/错误行已测） |

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

### P1（已完成）

- [x] `fix/whisper_fixer.py`（93%）与 `fix/fragment_processor.py`（90%）：VAD 缓存闸门、Tier1 三分类、Tier2/重试、AI 片段生成与应用。
- [x] `nouns/find_suspect_nouns.py`（74%）的 `_cluster_by_source` / `search_corrections` / `_apply_correction_fixes` / `_format_candidates`。
- [x] `reuse/align.py`（96%）、`reuse/apply.py`（98%）：R1–R6 采纳规则、复核缓存与失败、锚点链、dry-run/写盘、CLI 主流程。
- [x] `nouns/noun_checker.py`（79%）与 `nouns/apply_map.py`（96%）的主流程：提取模式、表格解析、运行替换、清单与映射导出。
- [x] `run_all.step_scan/step_nouns/step_apply_all/step_deliver/step_clean` 的副作用分支（命令参数、缓存命中、报告写入、降级报告、dry-run）。
- [x] `main()` 剩余资源/flag 分支：`--input-dir .`、`--episodes/--limit`、`--no-skip-if-clean`、dry-run ASS、zh 润色确认、AI review 提示。

### P2（已完成 / 剩余）

- [x] `scan/unified_scanner.py` 的 `scan_all` / VAD / main（94.6%）。
- [x] `apply/apply_fixes.py` 的 main、ASS 分支、繁→简（100%）。
- [x] `qwen3_worker` 的 chunk 分支（fake `model.transcribe`，99%）与 `qwen3_transcribe` CLI（99%）。
- [x] `whisper_utils` 的音频/VAD 段（45% → 100%）：ASS 解析、`vad_filter_audio`、抽音频、人声分离。
- [x] `episode_workflow`（36% → 100%）、`reuse/*`（73.8% → 97.9%）、`eval/asr_eval`（47% → 99%）。
- [x] `scripts/eval/clean_cc_reference.py`（98%，11 项）：日文配信 CC 去标签/振假名/♪，供金标评测前置。
- [ ] `nouns/auto_classify.py`、`nouns/auto_clean_glossary.py`（0%，旧入口，不在主链；若保留则补最小冒烟）。
- [ ] `lib/whisper_backends.py`（59%）、`asr/align_cues.py`（78%）、`fix/whisper_pipeline.py`（81%）。
- [ ] **待决策 / 清理**：`fix/episode_workflow._write_ai_review_json` 无调用方（死代码）；`--mode` 是已废除的残留 flag。保留则文档标注，删除则连同测试一起移除。

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

# 第四轮（run_all 步骤 / scanner / apply / worker / CLI / ass / reuse）
python -m pytest -q tests\test_run_all_step_functions.py tests\test_unified_scanner.py `
  tests\test_apply_fixes.py tests\test_qwen3_worker.py tests\test_qwen3_cli.py `
  tests\test_ass_repair.py tests\test_reuse.py

# 第五轮（whisper_utils / episode_workflow / reuse / eval / CC 清洁）
python -m pytest -q tests\test_whisper_utils.py tests\test_episode_workflow.py `
  tests\test_reuse.py tests\test_reuse_common.py tests\test_asr_eval.py `
  tests\test_clean_cc_reference.py

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

当前 **79.9%**。按缺口补，不追求一次到 85%：

| 阶段 | 目标 | 重点 |
|---|---:|---|
| 基线 | 36% | 起点 |
| 上上轮 | 64.0% | P0 链路 + 相邻联动 + 修复/片段/复用/专名补齐 |
| 上轮 | 74.0% | run_all 步骤、scanner、apply、worker、ass/reuse CLI |
| 再上轮 | 79.0% | whisper_utils、episode_workflow、reuse、asr_eval、CC 清洁 |
| **现在** | **79.9%** | 时间轴质量、三集基准、display linger、diagnose/benchmark |
| 下一轮 | ≥82% | 根脚本 65.8%、nouns 67.2%、whisper_backends 59%、align_cues 78% |

> 覆盖率不是目的：**关键路径（扫描→修复→专名→翻译→应用→交付）每个分支至少有一条测试**，比总百分比更重要。
> 0 覆盖的旧脚本（`auto_classify` / `auto_clean_glossary` / Whisper 批处理与补丁）不在主链，先确认是否保留再决定补测或删除。

## 7. 顺带修掉的真 bug（都有回归测试）

| 位置 | 问题 | 测试 |
|---|---|---|
| `lib/whisper_utils.separate_vocals` | 裸相对文件名（如 `ep.wav`）且未传 `output_dir` 时 `dirname=''`，`os.makedirs('')` 在调用 demucs 前抛 `FileNotFoundError` | `test_bare_filename_defaults_to_current_directory` |
| `fix/episode_workflow.step_translate` | 成功日志硬编码 `→ ja`，`--lang zh` 时误导 | `test_step_translate_runs_translator_with_project_pythonpath` |
| `run_all._step_noun_classify` / `step_ai_review` | 扫描产物（`*_nouns.json`、`ai_fragments_*.json`）被批量枚举；单个文件截断/损坏时 `load_json` 抛 `JSONDecodeError`，直接打死 Phase 1/2/3 | `test_step_noun_classify_without_unknowns_skips_ai`、`test_step_ai_review_counts_pending_fragments` |
| `apply/apply_fixes` ASS `replace_name` | `build_dialogue_line` 复用 `_raw_parts` 只刷新 text，Name 改动被丢弃；报告仍显示 applied | `test_main_ass_replace_text_name_and_delete` |
| `apply/apply_fixes._run_trad_to_simp` | 只走 ASS `split(',', 9)` 路径，SRT 行永不匹配，`--trad-to-simp` / `--lang zh` 在 SRT 项目上始终 0 changed | `test_run_trad_to_simp_converts_srt_text`、`test_main_trad_to_simp_flag_converts_srt` |
| `asr/qwen3_worker._segments_from_asr_text` | 单句文本用 token 拼接，句内逗号/顿号/英文空格丢失（`お前、何してんだよ！`→`お前何！`） | `test_single_sentence_keeps_internal_punctuation`、`test_single_segment_keeps_spaces` |
| `asr/qwen3_worker.main` | 合法 JSON 但非对象（`[]`/`123`/`"x"`/`null`）时 `request.get` 抛 `AttributeError`，长驻 worker 直接退出 | `test_bad_json_lines_do_not_kill_worker`、`test_once_handles_one_line` |
| `translate_srt.load_glossary` | 表头被当成一条「日语→中文」映射；`build_glossary` 的「日语\|次数\|变体」表被当译名表，数字会被预替换进正文 | `test_load_glossary_skips_header_but_keeps_mapping`、`test_load_glossary_ignores_count_tables` |
| `nouns/aggregate.py`、`extractor.py`、`translate_srt.py` | `os.path.relpath` 在 Windows 跨盘（temp 在 C:、项目在 D:）抛 `ValueError`，日志行能打死整条流水线 | `test_main_init_seed_and_merge_sidecar` 等（改用 `lib.project_utils.display_path`） |
| `fix/oped_fill._apply_templates` | `write_subtitles(cues, fpath)` 参数顺序反了，歌词模板永远写不回文件 | `test_apply_templates_fills_only_blank_op_ed_cues` |
| `reuse/align.pair_up` | 双重 `zip` 让 work 返回值多包一层，`r` 恒为批次列表；所有候选一律缓存 verified（LLM 复核失效） | `test_pair_up_aborts_when_all_checks_fail`、`test_pair_up_reuses_cache_without_llm` |
| `nouns/noun_checker` 中文呼称 | 正则不识别全角逗号（`喂，阿童木！`），呼称候选漏检 | `test_chinese_extract_suffix_calling_intro_quoted` |
