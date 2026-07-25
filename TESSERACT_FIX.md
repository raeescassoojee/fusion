# Tesseract test fix

The previous test configuration forced the external Tesseract binary for all pipeline tests, including tests that did not use OCR. On a Windows machine with only the Python `pytesseract` package installed, this caused four failures.

Changes:

- Automatic discovery of common Windows Tesseract installation paths.
- OCR-dependent tests are skipped with a clear explanation when no OCR engine is installed.
- Tests unrelated to OCR no longer fail because Tesseract is absent.
- `setup_windows.ps1` can install Tesseract through winget.
- `run_full_backend_test.ps1` reports the exact install command and continues with non-OCR tests.

For the full 16/16 camera test result on Windows, install Tesseract and rerun:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
.\scripts\run_full_backend_test.ps1
```
