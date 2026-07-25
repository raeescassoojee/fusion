$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Resolve Tesseract even when its installer did not add it to the current PATH.
if (-not $env:TESSERACT_CMD) {
    $tesseractCommand = Get-Command tesseract -ErrorAction SilentlyContinue
    $candidatePaths = @(
        if ($tesseractCommand) { $tesseractCommand.Source }
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
        "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe"
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($candidatePaths) {
        $env:TESSERACT_CMD = $candidatePaths[0]
        Write-Host "Using Tesseract: $env:TESSERACT_CMD"
    } else {
        Write-Warning "Tesseract 5 is not installed. OCR-specific tests will be skipped; the rest of the suite will still run."
        Write-Warning "Install it with: winget install --id UB-Mannheim.TesseractOCR -e"
    }
}

if (-not (Test-Path ".venv")) {
    py -3.11 -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

Write-Host "Installing camera AI..."
python -m pip install -e ".[dev]"

Write-Host "Installing operations service..."
python -m pip install -e ".\services\operations[dev]"

Write-Host "Running camera AI tests..."
python -m pytest tests

Write-Host "Running operations tests..."
python -m pytest services\operations\tests

$env:PYTHONPATH = "$Root\services\operations\src;$Root\src"
Write-Host "Running full backend integration test..."
python scripts\test_full_integration.py

Write-Host ""
Write-Host "All backend components passed."
Write-Host "To launch the dashboard:"
Write-Host '  $env:PYTHONPATH = ".\services\operations\src;.\src"'
Write-Host "  python -m uvicorn sentinel_ops.main:app --reload"
Write-Host "Then open http://127.0.0.1:8000/dashboard"
