$ErrorActionPreference = "Stop"
& .\.venv\Scripts\Activate.ps1

python -m sentinel_camera_ai --config config\default.yaml synthesize-demo --output media

$cam1 = python -m sentinel_camera_ai --config config\default.yaml process `
    --input media\camera_1_clip.mp4 `
    --camera-id CAM01 `
    --mode HEIGHTENED `
    --timestamp "2026-07-24T21:07:00+02:00" | ConvertFrom-Json

$cam2 = python -m sentinel_camera_ai --config config\default.yaml process `
    --input media\camera_2_clip.mp4 `
    --camera-id CAM02 `
    --mode HEIGHTENED `
    --timestamp "2026-07-24T21:07:04+02:00" | ConvertFrom-Json

$event1 = $cam1.events | Where-Object { $_.plate -eq "AB12CDGP" } | Select-Object -First 1
$event2 = $cam2.events | Where-Object { $_.plate -eq "AB12CDGP" } | Select-Object -First 1

if (-not $event1 -or -not $event2) {
    throw "The expected synthetic plate event was not produced."
}

python -m sentinel_camera_ai --config config\default.yaml compare `
    --event-a $event1.event_json `
    --event-b $event2.event_json `
    --output output\comparison.json

Write-Host "Demo complete. See output\events, output\evidence and output\comparison.json"

