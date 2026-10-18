# tests/

**能力靠测试证明，不靠真实项目。** 真实素材只用于发现问题；修之前先抽最小 fixture 测试。

## 运行

```powershell
python -m pytest -q                              # 全量，秒级，无网络/模型
python -m pytest -q --cov --cov-report=term-missing   # 覆盖率（需 pytest-cov）
python -m pytest -q tests/test_cue_mapper.py -k duplicate
```

## 约定

- 不联网、不下载模型、不读真实项目；外部边界（ffmpeg / whisper-cli / LLM / worker / janome / jieba / jamdict）只在 seam mock。
- 文件系统用 `tmp_path`；时间/随机固定。
- 断言行为（状态、文本、时间码），不断言常量或私有实现。
- Tier 1 纯函数 → Tier 2 tmp_path → Tier 3 mock 外部二进制。
- 源文件一律 UTF-8；不要用 PowerShell `Get-Content | Set-Content` 往返修改（中文会变 mojibake）。

## 覆盖现状与缺口

当前 **866 passed / 56.3% 覆盖**（无网络、无模型、无真实项目）。
P0 链路（Whisper 编排/VAD、翻译、专名、OP/ED、编排）已补齐；
剩余 P1 缺口（whisper_fixer、find_suspect 聚类、reuse、run_all 各 step）见
[`docs/test-plan.md`](../docs/test-plan.md)：能力矩阵与渐进覆盖率门槛。
