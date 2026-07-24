$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' was not found. Install Python 3.11 first."
}

py -3.11 -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[aws,yolo,dev]"

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Warning "Tesseract is not on PATH. Install Tesseract 5 or set TESSERACT_CMD."
}

python scripts\download_models.py
python -m sentinel_camera_ai --config config\default.yaml doctor

Write-Host "Setup complete. Run .\scripts\run_demo.ps1"

