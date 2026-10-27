# tests/

**能力靠测试证明，不靠真实项目。** 真实素材只用于发现问题；修之前先抽最小 fixture 测试。

## 目录按文档结构组织

一个目录对应一份文档，看哪份文档就进哪个目录：

| 目录 | 对应文档 | 覆盖内容 |
|---|---|---|
| `research/` | [references/research.md](../references/research.md) | 字幕站检索、视频扫描、专名表（抽取 / 归并 / 校验 / 改名） |
| `acquire/` | [references/acquire.md](../references/acquire.md) | B 站、种子站、p2p 派单确认、远程守卫 |
| `subtitle/` | [references/subtitle.md](../references/subtitle.md) | 扫描、ASR / Whisper、打轴与对齐、修复、OP-ED、复用、`run_all` |
| `translation/` | [references/translation.md](../references/translation.md) | 翻译、QA、润色、重译判定、worker / CLI |
| `shared/` | [SKILL.md](../SKILL.md)、[references/config.md](../references/config.md)、[capabilities.json](../capabilities.json) | 配置、项目路径、文档路由、能力清单、schema、发布闸门 |

`conftest.py`（共享 fixture）与 `data/`（小样本）留在 `tests/` 根；
新加测试按"它验证的那份文档"放进对应目录，不要平铺在根。

## 运行

```powershell
python -m pytest -q                                    # 全量
python -m pytest -q tests/subtitle                     # 只跑字幕链路
python -m pytest -q tests/translation/test_translate_srt.py -k duplicate
python -m pytest -q --cov --cov-report=term-missing    # 覆盖率（需 pytest-cov）
```

## 约定

- 不联网、不下载模型、不读真实项目；外部边界（ffmpeg / whisper-cli / LLM / worker / janome / jieba / jamdict）只在 seam mock。
- 文件系统用 `tmp_path`；时间/随机固定。
- 断言行为（状态、文本、时间码），不断言常量或私有实现。
- Tier 1 纯函数 → Tier 2 tmp_path → Tier 3 mock 外部二进制。
- 源文件一律 UTF-8；不要用 PowerShell `Get-Content | Set-Content` 往返修改（中文会变 mojibake）。
- 进子目录后仓库根是 `Path(__file__).resolve().parents[2]`。

## 覆盖现状

当前 **1853 passed / 4 skipped**（无网络、无模型、无真实项目，2026-10-27）。
主链（Whisper 编排 / VAD、翻译、专名、OP/ED、`run_all` 各 step、scanner、apply、
worker / CLI、`whisper_utils`、`episode_workflow`、reuse、eval）已覆盖；
缺口按文档分册记在各目录的用例里，补测时先补夹具再补断言。

skill 自身的结构也有人守：`tests/shared/test_pipeline_docs.py` 锁主文档 ≤150 行、
五段路由、细节已下沉；`tests/shared/test_capabilities.py` 锁 `capabilities.json` 的
每个能力都有入口 / 文档 / 触发词（全量 `python scripts/capabilities.py --report` 看矩阵）。

OP/ED 这条线现在是三件套，测试都在 `tests/subtitle/`：

| 能力 | 测试 | 锁什么 |
|---|---|---|
| 音频指纹定窗口 `audio/oped_boundaries.py` | `subtitle/test_oped_boundaries.py`（11 项） | 参考段解析、频带特征归一、已知位移检出（±0.15s）、缺席时返回 null、搜索窗口、集号解析 |
| 窗口内换定本 `fix/oped_region.py` | `subtitle/test_oped_region.py`（7 项） | 按字数分配时长、行数多于/少于原 cue、区域外不动、默认写副本、`--in-place` 造 `.bak`、`--dry-run` 不写 |
| 编排透传 `video_to_zh.py` | `subtitle/test_video_to_zh.py`（5 项） | `--chunk-sec/--max-batch` 透传、不给时不往命令行塞 0、转写/翻译命令拼装 |
