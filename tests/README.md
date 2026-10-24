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
| `shared/` | [SKILL.md](../SKILL.md)、[references/config.md](../references/config.md) | 配置、项目路径、文档路由、schema、发布闸门 |

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

当前 **1817 passed / 4 skipped**（无网络、无模型、无真实项目）。
主链（Whisper 编排 / VAD、翻译、专名、OP/ED、`run_all` 各 step、scanner、apply、
worker / CLI、`whisper_utils`、`episode_workflow`、reuse、eval）已覆盖；
缺口按文档分册记在各目录的用例里，补测时先补夹具再补断言。
