# 排错：症状 → 操作

先按症状查这一页；根因、实现细节和实验数据在开发记录里，不随 skill 同步。

## 1 跑不起来

| 症状 | 操作 |
|---|---|
| `SyntaxError` / `UnicodeEncodeError` | emoji→ASCII、括号补全，修完重跑 |
| `Done: 0 fixed` 且无 `[whisper]` 输出 | `--video-dir` 缺失或路径错；先 `--dry-run` 看资源检测结果 |
| `LLM_API_KEY not set`（翻译/润色） | 配置缺失时明确失败或保留原文；不降级到传统机翻（已移除） |
| `HTTP 400 invalid model` | 用 `deepseek-flash`，或更新 `lib/config.py:LLM_MODEL_DEFAULT` |
| 某步骤失败但已写中间文件 | 清空 `data/temp/` + `data/reports/`，加 `--force-rescan` 重跑；改 SRT 前先 commit |

## 2 结果不对

| 症状 | 操作 |
|---|---|
| 翻译 exit 0 但成片仍是日文 | 推理 token 吃光预算：`LLM_REASONING_EFFORT=none`，必要时提高 `LLM_MAX_TOKENS` |
| `--repair` 后 `echo` / 术语全灭 | `--input` 给了译文而不是源文；QA 在拿中文和中文比 |
| 整段译文错位一格 | 模型把两条并成一条或多吐一条；查 `[translate] ... returned N item(s)` 日志 |
| 译文带编号原文（`1. 原文`） | glossary 回显；说明文字写 CLAUDE.md，不要写进 JSON |
| 参考字幕乱码 / 解析 0 cue | 编码探测失准：查 BOM 与转码；ASS 惯例 UTF-16LE+BOM |
| 时间轴出现 0 时长 cue | 用已有 ASR JSON 重对齐（`run_all --asr qwen3`），或按 cue 字数补时长 |

## 3 OP/ED 与 ASS

| 症状 | 操作 |
|---|---|
| OP/ED 对白被歌词覆写 / 变少 | 检查窗口与歌词判定；与日文源逐条对数量 |
| 唱歌区间文本乱写 | 唱词以定本歌词为准，ASR 文本不参与判定 |
| ASS 内容 / 编码 / 字段异常 | `ass_repair.py --check all`；ASS 写 UTF-16LE+BOM |

## 4 资源与外部

| 症状 | 操作 |
|---|---|
| 迅雷派单被拒（远程会话） | 远程协作期间不开迅雷；等会话结束，或用户明确同意后加 `--allow-thunder` |
| 字幕站检索全失败 | 记缺口继续（不是所有作品都有字幕）；先确认代理可用，再按站点字段人工核对 |
