# Sentinel Camera AI verification report

Build date: 2026-07-25  
Project version: 0.1.0  
Event schema: 1.0

## Verified in this build

- Python source compiles successfully.
- 16 automated tests pass.
- Safe two-camera synthetic clips produce valid event JSON.
- The mock South African-style plate `AB12CDGP` is detected and read in both
  clips.
- Both clips produce `Blue` / `Car` / `right` vehicle evidence.
- Camera Trust Scores are 79/100 and 78/100 for the selected vehicle events.
- Cross-camera vehicle comparison returns `true`, score `1.0`, evidence
  strength `HIGH`.
- A fictional synthetic portrait produces one YuNet face detection at
  approximately `0.895` confidence.
- The face crop and SFace anonymous embedding are saved.
- A repeat comparison of the synthetic face returns `true`, score `1.0`,
  evidence strength `HIGH`.
- NORMAL mode skips face, plate OCR and detailed appearance processing.
- HEIGHTENED mode runs the full camera intelligence path.
- JSON Schema validation succeeds for the selected vehicle event.
- Missing Ultralytics and PaddleOCR packages select labelled fallbacks instead
  of crashing the controlled demonstration.

## Not falsely claimed

- Docker could not be executed in the build environment, so the Docker files
  were reviewed but not runtime-tested here.
- No live AWS account was mutated. The S3/API publisher is unit-tested with
  fake clients; use the guide's staged AWS validation before enabling it.
- The included OpenCV contour fallback and LPD-YuNet model are not proof of
  real South African CCTV accuracy. Add a properly licensed, locally validated
  plate-specific YOLO model before evaluating real footage.
- The synthetic face repeat uses the same authorized fixture. Calibrate face
  thresholds using consented same/different pairs before any deployment.
- No computer-vision model is failure-proof. Uncertain results remain
  `Unknown`, `null`, or a low-evidence candidate for human review.
