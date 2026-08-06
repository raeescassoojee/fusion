# MzansiMesh - one command to get everything running.
#
#   .\start.ps1              normal start
#   .\start.ps1 -Fresh       rebuild the venv from scratch
#   .\start.ps1 -Port 8080   different port
#
# Safe to re-run. Skips work that is already done.

param(
    [switch]$Fresh,
    [int]$Port = 8000,
    [int]$ChatPort = 8082,
    [switch]$NoChat,
    [switch]$SkipOcrInstall
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "  MZANSIMESH" -ForegroundColor Cyan
Write-Host "  Community safety network" -ForegroundColor DarkGray
Write-Host ""

# --- python -----------------------------------------------------------------
$python = $null
foreach ($candidate in @("py -3.11", "py -3.12", "py -3", "python")) {
    $parts = $candidate -split " "
    if (Get-Command $parts[0] -ErrorAction SilentlyContinue) {
        $version = & $parts[0] $parts[1..($parts.Length-1)] --version 2>&1
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
}
if (-not $python) {
    Write-Host "  Python not found. Install Python 3.11 or 3.12 from python.org" -ForegroundColor Red
    Write-Host "  and tick 'Add Python to PATH' during setup." -ForegroundColor Red
    exit 1
}
Write-Host "  python    $python" -ForegroundColor DarkGray

# --- venv -------------------------------------------------------------------
if ($Fresh -and (Test-Path ".venv")) {
    Write-Host "  removing old .venv ..." -ForegroundColor DarkGray
    Remove-Item -Recurse -Force .venv
}
if (-not (Test-Path ".venv")) {
    Write-Host "  creating .venv ..." -ForegroundColor DarkGray
    $parts = $python -split " "
    & $parts[0] $parts[1..($parts.Length-1)] -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

# --- dependencies -----------------------------------------------------------
$stamp = ".venv\.deps-installed"
$needInstall = $Fresh -or (-not (Test-Path $stamp)) -or `
               ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item $stamp -ErrorAction SilentlyContinue).LastWriteTime)
if ($needInstall) {
    Write-Host "  installing dependencies (a few minutes the first time) ..." -ForegroundColor DarkGray
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt
    New-Item -ItemType File -Path $stamp -Force | Out-Null
} else {
    Write-Host "  dependencies  already installed" -ForegroundColor DarkGray
}

# --- plate OCR --------------------------------------------------------------
function Find-TesseractExecutable {
    $command = Get-Command tesseract -ErrorAction SilentlyContinue
    $candidates = @(
        if ($env:TESSERACT_CMD) { $env:TESSERACT_CMD }
        if ($command) { $command.Source }
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
        "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe"
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    return $candidates | Select-Object -First 1
}

$tesseractPath = Find-TesseractExecutable
if (-not $tesseractPath -and -not $SkipOcrInstall) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "  plate OCR  Tesseract missing - installing it now ..." -ForegroundColor Yellow
        & winget install --id UB-Mannheim.TesseractOCR -e --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity
        $tesseractPath = Find-TesseractExecutable
    }
}

if ($tesseractPath) {
    $env:TESSERACT_CMD = $tesseractPath
    Write-Host "  plate OCR  Tesseract ready: $tesseractPath" -ForegroundColor Green
} else {
    Write-Host "  plate OCR  DISABLED - Tesseract 5 is not installed" -ForegroundColor Red
    Write-Host "             Run: winget install --id UB-Mannheim.TesseractOCR -e" -ForegroundColor Yellow
    Write-Host "             Then close this terminal and run .\start.ps1 -Fresh again." -ForegroundColor Yellow
}

# --- run --------------------------------------------------------------------
$env:PYTHONPATH = ".\services\operations\src;.\src"
if (-not $env:SENTINEL_PLATE_SALT) { $env:SENTINEL_PLATE_SALT = "pilot-demo-salt" }

Write-Host ""
Write-Host "  community   integrated with the dashboard API and SQLite" -ForegroundColor Green
Write-Host "  dashboard   http://127.0.0.1:$Port/dashboard" -ForegroundColor Green
Write-Host "  api docs    http://127.0.0.1:$Port/docs" -ForegroundColor Green
Write-Host "  pitch site  deliverables\site\pitch-site.html  (open directly)" -ForegroundColor Green
Write-Host ""
Write-Host "  Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

Start-Sleep -Seconds 1
Start-Process "http://127.0.0.1:$Port/dashboard"
python -m uvicorn sentinel_ops.main:app --port $Port --reload
