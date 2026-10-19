# FireRedVAD 接入状态（2026-10）

> 结论：**模型能用，项目接线能跑，但还没有“决策消费者”**。
> 现在只能做诊断/证据；不能算完整支持 OP/ED 翻译。

## 1. 已具备

| 能力 | 状态 | 入口 |
|---|---|---|
| VAD（speech 时间） | ✅ 已下载/实跑 | `models/FireRedVAD/VAD` + `fireredvad` |
| AED（speech/singing/music） | ✅ 已下载/实跑 | `models/FireRedVAD/AED` |
| Stream-VAD | ✅ 权重在 | `models/FireRedVAD/Stream-VAD` |
| 独立 CLI | ✅ | `scripts/audio/detect_events.py --wav ... --out ... --mode aed` |
| AudioHints 契约 | ✅ | `lib/audio/base.py`，`AudioEvent`/`AudioHints` |
| 修复链挂接 | ✅（仅 write） | `run_all --asr qwen3 --audio-hints` → `fix_by_asr(audio_hints=True)` → `<EP>.audio_hints.json` |
| 测试 | ✅ | `tests/test_audio_hints.py`（fake provider）、3 集真实实验 |
| 文档 | 🟡 部分 | `docs/model-assets.md`、`docs/capability-tests.md`、ADR-011；本文件补齐状态 |

## 2. 关键限制

1. **write-only**：生产链路没有任何模块读取 `<EP>.audio_hints.json`。
2. **只在 `--asr qwen3` 下触发**：默认 Whisper 路线不会调用 FireRedVAD。
3. **不能改时间轴**：以下方案都未通过多集验证：
   - speech-only concat：EP002/3 CER .503/.606；
   - token 过滤：EP002/3 coverage .335/.274；
   - 首尾裁剪：EP001 P90 2957ms；
   - AED 边界切块：P90/可靠变差。
4. **没有 OP/ED 消费者**：
   - 当前主窗口 CC 不含 OP/ED cue（EP001 从 99.5s 开始，末尾是次回预告）；
   - 没有“完整集 OP/ED 翻译”输入；
   - 没有 cue 级歌词分类、路由、评估。

## 3. 对 OP/ED 翻译还缺什么

要做完整集 OP/ED 翻译，至少需要：

1. 完整集音频/字幕输入（不是主窗口 `EPxxx.main.srt`）；
2. OP/ED 区间定义（片头/片尾、变体、每集差异）；
3. AED singing 区间 → **cue 级歌词分类**（precision/recall 要有标签）；
4. 歌词路由：OP/ED 走歌词翻译，不按对白；
5. 与 `oped_fixer` / `translate_srt` 的 OP/ED 逻辑接通；
6. 评估：歌词错译数/人工抽检，不只用“检测到 singing”。

## 4. 当前能否用于项目

- **能**：单独跑 `detect_events.py` 产出 hints；`--asr qwen3 --audio-hints` 能写 hints；
- **不能**：让 hints 改变 OP/ED/翻译/时间轴决策；
- **可用场景**：调查、OP/ED 预研、空洞诊断；
- **不可用场景**：声称“已支持 OP/ED 翻译”或“VAD 提升了时间轴”。

## 5. DSH skill 列表

- 之前 **没有**：`subtitle-proofread` 只在 `~/.claude/skills`，DSH 只扫描
  `<project>/.dsh/skills`、`<project>/.agents/skills`、`~/.dsh/skills`、`~/.agents/skills`。
- 已改为**同步副本**（`scripts/harness/sync_skill.py`，非 junction）：
  `~/.agents/skills/subtitle-proofread`
- 当前会话 catalog 已出现 `subtitle-proofread`，描述已改为 Qwen3-ASR/FA + FireRedVAD 诊断状态。
- 仓库仍是唯一开发源；harness 读取同步副本，本机配置在副本 `local.json`。

## 6. 下一步（OP/ED 研究）

1. 选 3 集完整音频，定义 OP/ED 真值区间；
2. 用 AED singing 算 cue 级 precision/recall；
3. 只对 lyrics cue 做翻译路由，A/B：按对白翻 vs 按歌词翻；
4. 有提升再接 `translate_srt`/`oped_fixer`；无提升就保持 diagnostics。
