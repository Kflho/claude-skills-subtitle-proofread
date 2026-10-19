# ADR-001: Fixer 上帝类拆分为 3 模块 + 薄 Orchestrator

- **Date**: 2026-07-24
- **Status**: ✅ Accepted
- **Context**: `fix/fix_orchestrator.py` 增长到 ~2200 行，一个类包含了路径解析、VAD、Whisper 转录、分诊、AI 碎片处理、SRT 写入、报告更新等所有职责。

## Decision

拆分为 4 个文件，数据契约驱动：

| 模块 | 行数 | 职责 | 返回 |
|------|------|------|------|
| `fix/subtitle_session.py` | ~300 | 路径解析、SRT/视频检测、VAD/Review 缓存 | SubtitleSession 对象 |
| `fix/whisper_fixer.py` | ~540 | VAD→聚类→Whisper→分诊 | `WhisperResult` dataclass |
| `fix/fragment_processor.py` | ~340 | AI 碎片 JSON 生成+apply+VAD 对齐 | counts |
| `fix/fix_orchestrator.py` | ~550 | 薄编排层，组合上述模块+统一写 SRT/报告 | `FixReport` dataclass |

**数据契约**: 模块返回结构化数据（`WhisperResult`, `FixReport`），orchestrator 负责写 SRT + 报告。临时文件由模块内部直接读写。

## Consequences

- ✅ 每个模块可独立理解（300-550 行 vs 2200 行）
- ✅ 可独立测试（WhisperFixer 和 FragmentProcessor 可 mock Session）
- ✅ 向后兼容 — Fixer 公共 API 不变
- ⚠️ FragmentProcessor 直接写 SRT（与 VAD 对齐耦合太紧，无法干净地解耦）
- ⚠️ `_apply_whisper_result()` 是唯一写 SRT+报告的地方 — 但如果 module 不小心绕过它直接写文件，会产生不一致
