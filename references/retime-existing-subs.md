# 文字正确、时间轴错位：重新打轴

## 适用

- 字幕文本抽查与台词一致，只是整体偏移、逐渐漂移、cue 边界切早/切晚；
- 已有字幕从别的片源/帧率/版本搬来，文字对、轴不对；
- 英文/日文字幕需要重新对到当前视频。

不适用：文本本身有错听、漏句、翻译错误。那要先修文本，再谈轴。

## 原则

1. **文本不动**：`asr/align_cues.py` 默认 time-only，`--write` 只写起止时间；
2. **先诊断后写盘**：不加 `--write` 先看 `summary`、`plan`、`alignment.json`；
3. **不要用 `--fix-text`**：它只用于乱码/错词；本场景加了会改字幕文字；
4. **一次只改轴**：先交付时间轴版本，再做翻译/润色，避免两个变量一起动。

## 文本语言与音频一致

```bash
# 1) 只诊断
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --video "<video>" \
  --language English --engine qwen3 \
  --python "<qwen-venv>/Scripts/python.exe"

# 2) 已有 ASR JSON 时跳过模型
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --asr-json "temp/asr/<EP>.json"

# 3) 确认后写回时间码
python "<scripts-dir>/asr/align_cues.py" \
  --srt "<subtitle.srt>" --asr-json "temp/asr/<EP>.json" --write
```

看报告里的 `summary`：匹配率、时间修复条数、需要人工复核的条数。`plan` 里
`auto=true` 的是安全时间修复；`review` 的逐条看，不要一把梭。

## 文本语言与音频不一致

英文/中文字幕配日语音轨时，cue 文本和 ASR 文本对不上，直接跑会把匹配率拉低，
甚至给出错误时间。按顺序选：

1. 优先找**源语言字幕**（日语原字幕）先对齐，再按句序把译文时间映射过去；
2. 没有源字幕：先转录源语言，按句序/参考字幕建立对应，再套译文；
3. 有同语言配音（例如英语配音）时，用该音轨的 ASR 对齐英文字幕；
4. 都做不到：只做整段平移/等间隔插值，并明确标为低置信度，不要伪装成逐句对齐。

上面 1–2 步的落地工具是 `asr/retime_crosslingual.py`（下一节）。

## 跨语言重打轴：ASR + LLM 锚点 + 分段 warp

适用：中文字幕是从英文字幕翻译来的，音轨是日语；文本相似度和语音活动对齐都
不可靠（实测对齐器对英字↔日音给出 0 条干净匹配）。

```bash
# 1) 先转写原音（时间戳要能用；Whisper large-v3 -mc 0 比 Qwen3 召回高）
whisper-cli -m <large-v3> -f EP001.wav -l ja -mc 0 -oj -osrt -of temp/asr/P01

# 2) 诊断 + 写新文件（永远不覆盖源字幕）
python "<scripts-dir>/asr/retime_crosslingual.py" \
  --source-srt "英文字幕/EP001.srt" \
  --asr-srt "temp/asr/P01.v3.srt" \
  --target-srt "translated/zh/EP001.srt" \
  --output "retimed/EP001.srt" --report "temp/retime/EP001.json"
```

原理：LLM 读两份有序文稿（译文 + 原音 ASR），按**语义**给出 cue→ASR 的单调映射，
脚本再用这些锚点拟合 `delta(t)` 分段线性 warp，只改时间码。

| 参数 | 用途 |
|---|---|
| `--source-srt` | 译文语言的字幕（提供对齐文本），通常是英文字幕 |
| `--asr-srt` | 原音 ASR，SRT/ASS；损坏 UTF-8 会先修复再读 |
| `--target-srt` | 要重打轴的字幕，默认同 `--source-srt`；必须与 source 条数 1:1 |
| `--output` | 新文件；已存在会拒绝覆盖 |
| `--report` | JSON：覆盖率、基线偏移、delta 分布、holdout 误差、移动条数 |
| `--anchors` | 复用锚点 JSON，不调 LLM（离线重跑/调参用） |
| `--baseline auto\|median\|zero` | 默认 auto：小于 1 s 的整集偏移当「字幕提前量」保留，只修结构性偏移 |
| `--dry-run` | 只出报告不写盘 |

**报告怎么读**（不通过就不交付）：

| 字段 | 期望 | 不达标怎么办 |
|---|---|---|
| `coverage` | ≥ 0.6（好素材 0.9+） | < 0.35 脚本直接退出 4；低覆盖先查**集号是否错位** |
| `holdout_error.p50` | < 0.5 s | 偏大说明 ASR 时间戳太粗（换 large-v3 / 关 `-mc` 上下文） |
| `holdout_error.p90` | < 2 s | 同上；或该集素材被剪过、锚点稀疏 |
| `local_medians` | 各时段都接近 0 | 某个 3 分钟桶明显非 0 → 那里真有结构性偏移，warp 会修 |
| `moved_max` | 与 `delta` 范围一致 | 远超 anchor delta 说明锚点有坏点，看 `delta_range` |

### 两个实测坑

1. **集号错位**：英文发行版按播出日编号，日版 DVD 可能另排（实测《佐武与市捕物控》
   英版 E01/E02 与 DVD P1/P2 是对调的：英版 E02 = DVD P1「反逆者」）。
   症状：片头固定台词（标题、OP 旁白）对得上，之后内容越走越偏，
   语音活动对齐会给出乱七八糟的几十秒偏移。
   **先验集号**：低覆盖时把 source 换到相邻集再跑一次；或比对字幕里的
   `Episode N: <标题>` 信息卡与视频标题。集号错位只能换文件，重打轴救不了。
2. **ASR 的 UTF-8 可能损坏**：whisper.cpp 偶尔把一个假名拆成非法字节，
   `read_subtitles` 会整文件回退 cp1251 → 日文变西里尔乱码 → 锚点全错
   （实测 EP010 因此出现「+23 秒」假偏移）。`retime_crosslingual` 会检测并用
   U+FFFD 修复后继续，并打印警告；看到 `warn: ... invalid UTF-8` 就该知道
   这份 ASR 需要重新导出。

## 参数（`asr/align_cues.py`，同语言场景）

| 参数 | 用途 |
|---|---|
| `--srt` | 待重打轴字幕 |
| `--asr-json` | 已有 ASR 结果，省模型时间 |
| `--audio` / `--video` | 16k WAV 或视频；二选一 |
| `--language` | ASR 语言，英文用 `English` |
| `--engine` | 默认 `qwen3`；有 Whisper 环境可用 `whisper` |
| `--write` | 写回时间码；不加只诊断 |
| `--fix-text` | **本场景不要用** |
| `--search-seconds` | 每 cue 在 ASR 里搜索的窗口 |
| `--max-shift` | 允许的最大单条位移，超出转人工 |
| `--timeline-source` | 时间轴来源策略，默认 auto |
| `--repair-overlong` | 修复超长 cue，按需 |

## 验收

- 抽 5–10 个密集对话点，看口型/停顿是否对上；
- 检查 `alignment.json` 的 review 条数，未处理完不算完成；
- 跑 `qa.py` 或 `run_all.py` 的 QA 步骤，确认无负时长/重叠/越界；
- 文本逐字对比原 SRT，确认没有被 `--fix-text` 改动；
- 保留 `.bak` 或版本控制，写回前可回滚。

跨语言场景（`retime_crosslingual.py`）额外看：

- `coverage` 与 `holdout_error` 达标（见上表），否则不交付；
- 用**第二条独立 ASR**（不同引擎/模型）复核 2–3 个集：两份 ASR 给出的偏移应一致；
- 抽查该集 `local_medians` 最大的一段，确认偏移确实存在（不是 ASR 时间戳抖动）；
- 若发现集号错位，交付前把**内容**换回正确集，而不是只改时间。
