# 故障排查 — 根因与实现细节

> 从 `SKILL.md`「错误恢复」移出的开发者说明。用户向“看到什么 → 做什么”短表现在在
> `references/troubleshooting.md`（主文档只留阶段路由）。
> 相关代码：`lib/subtitle_io.py`、`lib/llm.py`、`lib/config.py`、
> `fix/translate_srt.py`、`fix/oped_*.py`、`ass/ass_repair.py`。

## 1. 编码探测失准

**症状**：参考字幕乱码（西里尔 / 中文变 `?`），或**解析出 0 条 cue 却不报错**。

`_detect_encoding()` 的策略：

1. 先嗅 BOM（UTF-8 BOM / UTF-16LE / UTF-16BE）。
2. 无 BOM → 看 NUL 字节奇偶分布判断 UTF-16。
3. CJK 多字节编码（UTF-8 / GBK / Shift-JIS…）。
4. **单字节编码（cp1251 / koi8-r / latin-1）必须留在试探链末尾** ——
   它们对任意字节串都能解码成功，排在 CJK 多字节编码前会让后者成死代码。

ASS 惯例编码是 **UTF-16LE + BOM**；SRT 惯例是 **UTF-8-SIG**。
写出 ASS 时必须沿用模板编码，否则播放器识别异常。

## 2. LLM 空 content / 推理 token

**症状**：翻译 exit 0，但成片 cue 仍是日文原文；日志没有报错。

根因：推理模型思考 token 吃光 `max_tokens` → HTTP 200 + `content` 空 +
`finish_reason='length'`。调用方拿空串判失败，整批 cue 保留原文（静默）。

处理：

- 确认 `LLM_REASONING_EFFORT='none'`；空串 = 省略参数 = API 默认（思考开）。
- 不要把 `LLM_MAX_TOKENS` 无限加大；思考长度会跟着涨。
- 非 DeepSeek 端点若不认 `reasoning_effort`，设为空串省略该参数。
- `lib/config.py:apply_llm_params()` 是唯一补参数入口；自拼请求体的脚本应走它。

实测数据见 `dev/translation-pipeline.md` §3。

## 3. Glossary 回显 / 编号污染

**症状**：译文里成片出现 `1. 原文` `2. 原文`（带编号）。

根因：prompt 被 glossary 污染。`load_mappings` 会把**所有值**拼进
“固定译名参考”，`_comment` 之类的元数据键会混入。

- 已自动跳过 `_` 前缀键。
- **说明文字写进 CLAUDE.md，不要留在 JSON 里**；
  `apply_map.py --merge-existing` 等消费方未必过滤。

## 4. 接受循环把回显记成成功

**症状**：回显文本被记为“已翻译”。

根因：判据写成 `out != src`。回显返回的是**带编号的原文**，文本确实“不同”，
但没翻译。

正确：用 `is_untranslated()` 剥掉 `^\s*\d+\s*[.、)）]\s*` 前缀再比。
不要只比字符串不等。

## 5. OP/ED 预替换误伤对白

**症状**：

- 片头/片尾一大段对白变成同一句歌词中文。
- OP/ED 预替换后对白凭空少了一截。

根因（历史）：预替换按固定 180s 窗口无脑覆写，窗口宽于实际 OP。

现状：

- `_is_lyric()` 只覆写真歌词（等于窗口 canonical / 同集窗口内重复 ≥2 次 /
  跨集出现 ≥2 次）。
- 被覆写的 cue 文本非空、非日文，**Step 4 各验证项都不会报警** ——
  必须与日文源逐条对数量。
- 禁止再用固定时间窗判定 OP/ED；用相似度/重复度/AED 信号。

## 6. ASS 写出问题

| 症状 | 根因 | 处理 |
|---|---|---|
| 生成内容全是模板那一集的台词 | 新译文误走就地编辑分支（`_start_line` 对不上、不报错） | 输出文件须**事先不存在**才会走新文件模式（模板只提供 header/styles） |
| ASS 写出来变成 UTF-8 | `write_ass_file(..., template_path=)` 没传，编码没沿用 | ASS 交付要 UTF-16LE + BOM |
| 行字段整体错位一格 | `build_dialogue_line` 兜底分支把 `format`(`Dialogue: {layer}`) 和 `layer` 并列输出 | Events 是 10 字段，Layer 内嵌在首字段里 |

ASS 解析：`split(',', 9)` 防逗号破坏；跳过 Display 样式。
`ass/ass_repair.py --check all` 覆盖 names/styles/drawing/comment/oped 五类检查。

## 7. Whisper 批量转录：时间戳自愈

**症状**：`-p`（processors）> 1 时，分块边界之后时间戳集体塌成 0。

根因：whisper.cpp `whisper_full_parallel` 在某些 build 上不对第 2..N 块施加
时间偏移。

处理：脚本检测到此类损坏会自动改用 `-p 1` 重跑一次并打印告警，无需人工干预。
代价是慢；批量转录建议先 `-p 1` 保证正确性。

## 8. Whisper 长 cue / 切片修复

**症状**：全片 Whisper 在多人争吵/对话密集场景把多句合并成一条长 cue，
导致 VAD 分段失准、输出乱码；LLM 无力翻译时标 `[???]`。

根因：不是那几秒音频“特别难”，而是全片模式下 Whisper 的段边界/长 cue 合并。
`whisper_spot_fix.py` 用短窗口让内部 VAD 分段更准，输出 JA + ZH 参考。

> 2026 技术路线 P2 会用 Qwen3-ForcedAligner + 全局文本对齐替代
> “切段重跑 Whisper”；根因是段级时间戳，不是模型没跑对那 9 秒。

## 9. `Done: 0 fixed` 且无 `[whisper]` 输出

**症状**：跑完显示 0 修复，没有任何 Whisper 日志。

根因：`--video-dir` 缺失或路径错。

处理：验证 CLAUDE.md / 环境变量中的视频目录；先跑 `--dry-run` 看资源检测。

## 10. 中间文件污染

**症状**：某步骤失败，但已写中间文件；重跑读到旧状态。

处理：清空 `temp/` + `reports/`，加 `--force-rescan` 重跑。
修改 SRT 前务必 git commit（见 `dev/maintenance.md`）。
