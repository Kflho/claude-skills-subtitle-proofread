# CONTEXT.md — Subtitle Proofread Skill

## 项目是什么

一个 3-Phase 字幕校对流水线，支持日语/中文/英语。输入 SRT/ASS 字幕文件，通过字符扫描→Whisper ASR 修复→专有名词统一→交付审查，输出校对后的字幕。

## 架构图

```
Phase 1: SCAN                    Phase 2: FIX                    Phase 3: UNIFY + DELIVER
─────────────────                ─────────────────               ─────────────────────────
unified_scanner.py               fix_orchestrator.py             noun_checker.py
  ├─ garbled_cues ───────────────→ Fixer.run_auto()               find_suspect_nouns.py
  ├─ missing_subtitles ──────────→   ├─ WhisperFixer              oped_fixer.py
  └─ glossary ───────────────────→   │   (VAD→cluster→Whisper)   apply_fixes.py
                                      │                           step_deliver()
                                      ├─ FragmentProcessor
                                      │   (AI补全+VAD对齐)
                                      └─ → SRT + 问题解决报告
```

**数据契约链**: JSON 作为阶段间桥梁
```
findings.json → (Phase 2) → ai_fragments_{EP}.json → (Phase 3) → all_fixes.json
noun_check.json → suspect_nouns.json → ai_review_candidates.json → ai_review_fixes.json
```

## 模块地图

```
scripts/
├── lib/                        # 基础库（零副作用，被所有模块导入）
│   ├── config.py               #   单一配置源 — 所有 env var + 默认值
│   ├── subtitle_io.py          #   规范 I/O — read_subtitles/write_subtitles/apply_fixes_to_cues
│   ├── whisper_utils.py        #   文本分类/时间码/Whisper过滤
│   ├── whisper_backends.py     #   多后端Whisper抽象 (cpp/faster/openai)
│   ├── subprocess_utils.py     #   统一子进程 — run_ffmpeg/run_whisper/run_git
│   ├── project_utils.py        #   项目检测/资源发现
│   ├── ass_utils.py            #   ASS格式解析
│   ├── srt_utils.py            #   SRT格式构建
│   ├── language_utils.py       #   语言→工具模块分发
│   ├── chinese_utils.py        #   中文常词/繁简映射
│   ├── japanese_utils.py       #   日文常词/假名检测
│   └── english_utils.py        #   英文常词
│
├── fix/                        # Phase 2: 错误修复引擎
│   ├── fix_orchestrator.py     #   薄orchestrator — 组合WhisperFixer+FragmentProcessor
│   ├── subtitle_session.py     #   路径解析/检测/缓存 — 被orchestrator和worker共用
│   ├── whisper_fixer.py        #   VAD→聚类→Whisper转录→分诊 (数据契约:WhisperResult)
│   ├── fragment_processor.py   #   AI碎片JSON+apply+VAD对齐 (直接写SRT, pragmatic exception)
│   ├── oped_fixer.py           #   OP/ED跨集一致性修复
│   ├── compare_srt.py          #   字幕对比/相似度
│   ├── translate_srt.py        #   百度翻译+LLM翻译
│   └── episode_workflow.py     #   单集子进程工作流
│
├── scan/                       # Phase 1: 字符扫描
│   └── unified_scanner.py      #   乱码检测+VAD缺失字幕扫描
│
├── nouns/                      # Phase 3: 专有名词
│   ├── noun_checker.py         #   名词表对比
│   └── find_suspect_nouns.py   #   未收录疑似专名搜索
│
├── apply/                      # 批量修复应用
│   └── apply_fixes.py          #   JSON修复→SRT写入
│
├── ass/                        # ASS格式工具
│   └── ass_repair.py           #   ASS格式修补
│
├── utils/
│   └── update_report.py        #   问题解决报告读写 (JSON权威存储+MD导出)
│
├── translate_srt.py            # 独立翻译工具 (日→中)
├── polish_zh.py                # 独立润色工具 (去翻译腔)
├── auto_translate.py           # 专名自动校对
└── run_all.py                  # 顶层编排器 (唯一入口)
```

## 核心设计原则

1. **数据契约**: 模块间通过结构化数据（dataclass/dict/JSON）通信，orchestrator 统一写文件。例外: FragmentProcessor 直接写 SRT（与 VAD 对齐的耦合不可避免）。

2. **资源驱动**: 有什么用什么。有视频→Whisper修复；有参考字幕→AI注入上下文。无视频→残血运行（仅扫描+人工审查）。

3. **单一配置源**: `lib/config.py`。所有 env var 读取出处唯一。不直接 `os.environ.get()`。

4. **永不降级**: 报告条目状态 `✅ > 🗑️ > ⬜`，新状态优先级必须 ≥ 旧状态。

5. **CoW 写策略**: 修改 SRT 前 git commit 备份。所有工具原地覆写文件。

## 测试

```bash
cd <skill>/tests && pytest -q    # 234 tests, 0.5s, 无外部依赖
```

分层: Tier 1 (纯函数) → Tier 2 (tempfile) → Tier 3 (mock 外部二进制)。

## 配置

所有配置在 `lib/config.py`。环境变量在 CLAUDE.md 中设置。关键变量:
- `WHISPER_CLI` / `WHISPER_MODEL` — Whisper 路径
- `LLM_API_KEY` — LLM API key
- `BAIDU_APPID` / `BAIDU_SECRET` — 百度翻译（可选）
