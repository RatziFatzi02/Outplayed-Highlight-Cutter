$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ToolsDir = Join-Path $ProjectRoot ".tools"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install "PySide6==6.11.1" "python-snappy>=0.7.3,<0.8" "zstandard>=0.25,<0.26" "pytest>=9,<10"
# dfindexeddb pins old compression packages without Python 3.13 wheels.
& $VenvPython -m pip install "dfindexeddb==20260210" --no-deps
& $VenvPython -m pip install -e $ProjectRoot --no-deps

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
$ExistingFfmpeg = "D:\Projects\70_Tools-PortableApps\TwitchDownloader\ffmpeg.exe"
$BundledFfmpeg = Join-Path $ToolsDir "ffmpeg.exe"
if (-not (Test-Path -LiteralPath $BundledFfmpeg) -and (Test-Path -LiteralPath $ExistingFfmpeg)) {
    Copy-Item -LiteralPath $ExistingFfmpeg -Destination $BundledFfmpeg
}

Write-Host "Setup complete. Start with .\start.ps1"

