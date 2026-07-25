$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.11 first."
}

py -3.11 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[aws,yolo,dev]"

$tesseractCommand = Get-Command tesseract -ErrorAction SilentlyContinue
$candidatePaths = @(
    if ($tesseractCommand) { $tesseractCommand.Source }
    "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
    "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe"
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $candidatePaths) {
    Write-Warning "Tesseract 5 is not installed. Plate OCR will not work until it is installed."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Tesseract OCR with winget..."
        winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements
        $candidatePaths = @(
            "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
            "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe"
            "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
        ) | Where-Object { Test-Path $_ }
    }
}

if ($candidatePaths) {
    $env:TESSERACT_CMD = $candidatePaths[0]
    Write-Host "Using Tesseract: $env:TESSERACT_CMD"
} else {
    Write-Warning "Tesseract is still unavailable. Install it manually and set TESSERACT_CMD to tesseract.exe."
}

python scripts\download_models.py
python -m sentinel_camera_ai --config config\default.yaml doctor

Write-Host "Setup complete. Run .\scripts\run_demo.ps1"

