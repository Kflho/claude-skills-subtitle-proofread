# ADR-002: 数据模型统一 — 双模型 → 单一 Cue 列表

- **Date**: 2026-07-24
- **Status**: ✅ Accepted
- **Context**: 字幕数据在 3 条路径中被不同地表示 — `srt_utils` 用行列表，`whisper_utils` 用 cue 字典（不同 key 名），`apply_fixes` 用自建解析器。所有 3 个各自读写 SRT 格式。这导致了微妙的 bug（解析器之间缓存过期、行号偏移）以及重复的 parse/write SRT 代码。

## Decision

创建 `lib/subtitle_io.py` 作为**唯一的规范 I/O 入口**。所有模块通过它处理字幕 I/O：

```python
cues = read_subtitles(path)        → list[dict]  # 统一 cue 格式
write_subtitles(path, cues)        → None
apply_fixes_to_cues(cues, fixes)   → int  # 原地修改
```

旧函数（`parse_srt`, `write_srt`, `apply_fixes_to_srt`）委托到新的规范实现，保持向后兼容。

## Consequences

- ✅ 消除了双数据模型导致的一整类 bug（解析差异）
- ✅ 解析/格式化逻辑的单一点
- ✅ 向后兼容 — 旧调用者无需修改
- ✅ `replace_global` 改为逐 cue 替换（不跨行，更安全）
- ⚠️ `whisper_utils` 和 `srt_utils` 与 `subtitle_io` 之间的委托封装增加了一层间接性，可能让初学者困惑"真正实现在哪"
