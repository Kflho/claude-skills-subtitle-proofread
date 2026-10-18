param(
    [string]$Venv = ".venv-qwen",
    [string]$IndexUrl = "",
    [switch]$SkipInstall,
    [switch]$SkipCheck,
    [switch]$DownloadModels,
    [string]$AsrModel = "Qwen/Qwen3-ASR-1.7B",
    [string]$AlignerModel = "Qwen/Qwen3-ForcedAligner-0.6B"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root

Write-Host "== Qwen3 setup ==" -ForegroundColor Cyan
Write-Host "project: $root"
Write-Host "venv:    $Venv"

if (-not (Test-Path $Venv)) {
    Write-Host "creating venv (reusing system torch)..."
    python -m venv $Venv --system-site-packages
}

$py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "python not found: $py"
}

$pipArgs = @()
if ($IndexUrl) {
    $pipArgs = @("--index-url", $IndexUrl)
    Write-Host "using pip index: $IndexUrl"
}

& $py -m pip install @pipArgs -U pip
if (-not $SkipInstall) {
    Write-Host "installing qwen-asr==0.0.6 ..."
    & $py -m pip install @pipArgs -U "qwen-asr==0.0.6"
}

if ($DownloadModels) {
    $hf = Join-Path $Venv "Scripts\huggingface-cli.exe"
    if (-not (Test-Path $hf)) {
        & $py -m pip install -U "huggingface_hub[cli]"
    }
    $models = Join-Path $root "models"
    New-Item -ItemType Directory -Force $models | Out-Null
    Write-Host "downloading $AsrModel ..."
    & $py -m huggingface_hub.commands.huggingface_cli download $AsrModel --local-dir (Join-Path $models "Qwen3-ASR-1.7B")
    Write-Host "downloading $AlignerModel ..."
    & $py -m huggingface_hub.commands.huggingface_cli download $AlignerModel --local-dir (Join-Path $models "Qwen3-ForcedAligner-0.6B")
}

if (-not $SkipCheck) {
    Write-Host "checking ..."
    & $py scripts\asr\qwen3_transcribe.py --check
}

Write-Host ""
Write-Host "Next:" -ForegroundColor Green
Write-Host "  1. set env QWEN_PYTHON=$py"
Write-Host "  2. run a 30s WAV:"
Write-Host "     $py scripts\asr\qwen3_transcribe.py --audio sample.wav --timestamps --output temp\asr\sample.json"
Write-Host "  3. from the main env:"
Write-Host "     python scripts\asr\qwen3_transcribe.py --audio sample.wav --python $py --output temp\asr\sample.json"
Write-Host ""
Write-Host "Model download (if not -DownloadModels):" -ForegroundColor Yellow
Write-Host "  $py -m huggingface_hub.commands.huggingface_cli download $AsrModel --local-dir models\Qwen3-ASR-1.7B"
Write-Host "  $py -m huggingface_hub.commands.huggingface_cli download $AlignerModel --local-dir models\Qwen3-ForcedAligner-0.6B"
