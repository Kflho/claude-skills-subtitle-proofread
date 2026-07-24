# ADR-003: 报告当数据库 — Markdown 伪数据库 → JSON 权威存储

- **Date**: 2026-07-24
- **Status**: ✅ Accepted
- **Context**: `utils/update_report.py` 通过解析 Markdown 表格（`reports/问题解决报告.md`，7184 行）来提供 CRUD 操作。每次更新都需 `read_report(MD解析) → 修改 OrderedDict → write_report(MD生成)` 的全量重写。Markdown 解析脆弱（依赖精确的表格格式），且大文件全量读写性能差。

## Decision

JSON 替代 Markdown 作为权威存储，Markdown 降级为人类可读导出。

- **权威存储**: `temp/report.json` — 机器可读，每次 `write_report()` 写入
- **人类可读导出**: `reports/问题解决报告.md` — 每次 `write_report()` 自动重新生成
- **迁移**: 首次 `read_report()` 自动从现有 MD 解析→写入 JSON（一次性）
- **公共 API**: 零改动 — `read_report()`, `write_report()`, `upsert_entries()` 等签名完全相同，内部自动走 JSON 后端

## Consequences

- ✅ 读取性能: 13ms vs 解析 7184 行 MD（O(1) vs O(n)）
- ✅ 更健壮: JSON 解析不会因为格式微调而断裂
- ✅ 6 个调用者零改动
- ✅ MD 文件仍存在并可读
- ⚠️ `temp/report.json` 在 `.gitignore` 中 — 迁移后的 MD 文件仍是可读备份
