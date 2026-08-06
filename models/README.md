# Model files

Run:

```powershell
python scripts\download_models.py
```

This downloads the official OpenCV Zoo YuNet face detector, SFace embedding
model and LPD-YuNet plate detector, together with their upstream licence
notices.

Verify the bundled/downloaded model files:

```powershell
Push-Location models
Get-Content checksums.sha256 | ForEach-Object {
    $parts = $_ -split '\s+', 2
    if ((Get-FileHash $parts[1] -Algorithm SHA256).Hash.ToLower() -ne $parts[0]) {
        throw "Checksum failed: $($parts[1])"
    }
}
Pop-Location
```

For stronger licence-plate detection, place a tested and properly licensed
Ultralytics-compatible model at:

```text
models/license_plate_detector.pt
```

LPD-YuNet is trained on Chinese plates, so it is used as one candidate detector
and must not be treated as South African validation. The application ensembles
it with an explicitly labelled OpenCV contour fallback. A geographically tested
plate-specific YOLO model remains the preferred production option.
