# 环境安装与配置

> 仅首次使用时加载。配置完成后跳到核心流程。

## 第一步：确认用户资源

启动时询问用户：

> 请提供以下路径信息（不确定的留空）：
>
> | # | 路径项 | 用途 | 是否必需 |
> |---|--------|------|:---:|
> | 1 | **目标字幕目录** | 待校对字幕文件 | ✅ 必需 |
> | 2 | **参考字幕目录** | 高质量人工翻译字幕，用于对照 | 可选 |
> | 3 | **原语言字幕目录** | AI 生成的源语言字幕（如日语 SRT） | 可选 |
> | 4 | **视频文件目录** | mkv/mp4，用于 Whisper 提取音频 | 可选 |
> | 5 | **whisper.cpp 路径** | 如已安装 | 可选 |

> 根据你提供的资源，skill 将自动选择可用的工作模式：
> - 有 #2 → 启用完整模式（翻译验证）
> - 有 #3 → 启用 AI 字幕修复（罗马字检测、OP/ED 识别）
> - 有 #4 + #5 → 启用 Whisper 音频修复

## 第二步：扫描 Whisper 安装

Claude 自动扫描系统中是否存在 whisper.cpp（CUDA 版）：

```
检查路径: D:/software/video/whisper-cublas-*/whisper-cli.exe
检查模型: models/ggml-kotoba-whisper-v2.0-q5_0.bin (日语推荐)
备选模型: models/ggml-large-v3-q5_0.bin
```

**已有 whisper** → 记录路径和可用模型，跳到「验证环境」。

**没有 whisper** → 分析用户电脑配置：

```
- CPU: 核心数、架构 (x64/ARM)
- GPU: NVIDIA 显卡型号、VRAM 大小、CUDA 版本 (nvidia-smi)
- RAM: 总内存
```

根据分析结果推荐安装方案：

| 电脑配置 | 推荐方案 |
|----------|----------|
| NVIDIA GPU + 8GB+ VRAM | whisper.cpp CUDA 版 (cublas-12.4.0-bin-x64) |
| NVIDIA GPU + VRAM < 8GB | whisper.cpp CUDA 版 + 小模型 (q5_0 量化) |
| 无 NVIDIA GPU | whisper.cpp CPU 版 (avx2 优化) |

## 第三步：安装 Whisper（如需）

```
1. 手动下载对应版本的 whisper.cpp 压缩包
   - CUDA 版: https://github.com/ggerganov/whisper.cpp/releases → whisper-cublas-12.4.0-bin-x64.zip
   - CPU 版:  whisper-bin-x64.zip
2. 解压到 D:/software/video/whisper-cublas-*/  （避免中文路径）
3. 手动下载 GGML 模型（日语推荐 kotoba-whisper-v2.0）
   - https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-ggml/resolve/main/ggml-kotoba-whisper-v2.0-q5_0.bin
   - 放入 models/ 目录
4. 告诉 Claude 安装路径 → Claude 验证可执行文件和模型是否存在
```

> ⚠️ 下载失败一次就停止，不要反复重试 CLI。给用户浏览器下载链接和精确的目标路径。

## 第四步：Python 环境（人声分离 `--separate-vocals`）

**作用**：用 demucs AI 模型分离人声，去除 BGM/音效后再送 Whisper 转录。实测可显著减少幻觉（BGM 是 Whisper 幻觉的主要触发源）。**仅 GPU 用户推荐**（需 NVIDIA 显卡 + CUDA）。

### 4.1 确认 Python 3.12+ 为默认

```bash
python --version   # 必须: Python 3.12.x
pip --version      # 必须来自 Python 3.12
```

如果系统有多个 Python 版本，确保 Python 3.12 在 PATH 最前面。

### 4.2 安装 PyTorch（CUDA 12.4 版）

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
```

> 约 2.5GB，需稳定网络。其他 CUDA 版本见 https://pytorch.org/get-started/locally/

### 4.3 安装 demucs

```bash
pip install demucs
```

### 4.4 验证安装

```bash
python -c "import torch; print(f'PyTorch {torch.__version__}, CUDA: {torch.cuda.is_available()}')"
# 预期: PyTorch 2.x.x+cu124, CUDA: True

python -c "import demucs; print('demucs OK')"
# 预期: demucs OK
```

### 4.5 测试人声分离效果（可选）

```bash
# 从视频提取 30 秒测试音频
ffmpeg -y -ss 00:08:30 -t 30 -i "<视频>.mkv" -vn -ac 1 -ar 16000 _test_original.wav

# 分离人声（GPU 加速，约 2 秒）
python -m demucs --two-stems=vocals -o _test_sep _test_original.wav

# 对比 Whisper 转录
whisper-cli.exe -m <模型> -l ja -f _test_original.wav --no-timestamps
whisper-cli.exe -m <模型> -l ja -f _test_sep/htdemucs/_test_original/vocals.wav --no-timestamps
```

> 对比两次输出：人声分离后的转录应更连贯，幻觉词（填词/英文碎片）更少。

## 第五步：安装后用户需提供

安装完成后，用户需要告诉 Claude 以下信息：

| 信息 | 示例 | 用途 |
|------|------|------|
| whisper.cpp 安装目录 | `D:/software/video/whisper-cublas-12.4.0-bin-x64` | 定位 CLI 和模型 |
| 视频文件目录 | `E:/Animation/TV/[Anonymoose] 鉄腕アトム/` | 提取音频 |
| 原语言字幕目录 | `AI审查后/` | 乱码扫描输入 |
| 视频↔字幕对应规则 | 文件名中的集号匹配 | 自动配对 |

## 第六步：Claude 验证环境

Claude 确认以下检查全部通过后，skill 方可进入校对流程：

```bash
# 1. whisper-cli 可执行
whisper-cli.exe --help  # 应输出帮助信息，含 CUDA 设备

# 2. 模型文件存在
ls models/ggml-kotoba-whisper-v2.0-q5_0.bin

# 3. GPU 可用（CUDA 版）
whisper-cli.exe -m models/xxx.bin -f test.mp3 -l ja  # 输出应显示 CUDA device

# 4. ffmpeg 可用
ffmpeg -version

# 5. （可选）Python 3.12 + demucs 人声分离
python --version                          # Python 3.12.x
python -c "import torch; print(torch.cuda.is_available())"  # CUDA: True
python -c "import demucs; print('OK')"    # demucs OK
```
