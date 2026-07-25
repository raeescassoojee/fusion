$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

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
