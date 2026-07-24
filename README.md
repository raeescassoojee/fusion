# Discovery Sentinel Mesh - Camera AI

A local-first, modular camera intelligence pipeline for Person 3. It processes
images or recorded clips and produces:

- face presence boxes, crops and optional anonymous embeddings;
- licence-plate boxes, crops, normalized OCR text and confidence;
- vehicle type, colour and movement direction;
- visible upper/lower clothing colours;
- an explainable Camera Trust Score;
- NORMAL and HEIGHTENED processing modes;
- versioned event JSON;
- repeat-match decisions for vehicle, face and appearance;
- optional private S3 and backend API publishing.

The program degrades safely. Missing optional models do not crash the core
controlled demo: OpenCV/Tesseract fallbacks run and the JSON records which
fallbacks were used.

## Architecture

```text
image or recorded clip
        |
        v
OpenCV / YOLO detector adapters
        |
        +--> face crops and optional SFace embedding
        +--> plate crop and PaddleOCR/Tesseract
        +--> vehicle and appearance attributes
        |
        v
quality scoring + de-duplication
        |
        v
event JSON + annotated evidence
        |
        +--> local output
        `--> optional private S3 + backend API
```

## Quick start on Windows

Install:

- Python 3.11;
- Git;
- Tesseract 5 and add it to PATH;
- FFmpeg;
- Docker Desktop later, if needed.

Then run PowerShell:

```powershell
cd sentinel-camera-ai
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_windows.ps1
.\scripts\run_demo.ps1
```

If Tesseract cannot be placed on PATH:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## Manual installation

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[aws,yolo,dev]"
python scripts\download_models.py
python -m sentinel_camera_ai --config config\default.yaml doctor
```

PaddleOCR is optional because its engine installation differs by platform.
Follow the official PaddleOCR instructions, then install `paddleocr`. The
adapter uses PaddleOCR when available and falls back to Tesseract.

## Safe synthetic end-to-end demonstration

Create two synthetic clips:

```powershell
python -m sentinel_camera_ai --config config\default.yaml `
  synthesize-demo --output media --plate AB12CDGP
```

Process them:

```powershell
python -m sentinel_camera_ai --config config\default.yaml process `
  --input media\camera_1_clip.mp4 `
  --camera-id CAM01 `
  --mode HEIGHTENED `
  --timestamp "2026-07-24T21:07:00+02:00"

python -m sentinel_camera_ai --config config\default.yaml process `
  --input media\camera_2_clip.mp4 `
  --camera-id CAM02 `
  --mode HEIGHTENED `
  --timestamp "2026-07-24T21:07:04+02:00"
```

Compare the two vehicle event JSON files:

```powershell
python -m sentinel_camera_ai --config config\default.yaml compare `
  --event-a output\events\FIRST_EVENT.json `
  --event-b output\events\SECOND_EVENT.json `
  --output output\comparison.json
```

The included `scripts\run_demo.ps1` finds the correct synthetic plate events
automatically.

## Real media

Use short, controlled clips first:

```powershell
python -m sentinel_camera_ai --config config\default.yaml process `
  --input media\camera_1_clip.mp4 `
  --camera-id CAM01 `
  --latitude -25.797 `
  --longitude 28.301 `
  --mode HEIGHTENED `
  --timestamp "2026-07-25T14:30:00+02:00"
```

Use only consented team members or synthetic faces. Use mock or authorized
registration plates.

## Processing modes

`NORMAL`:

- samples fewer frames;
- runs basic person/vehicle detection;
- skips face embedding, plate OCR and detailed appearance.

`HEIGHTENED`:

- samples more frames;
- runs face, plate, OCR, vehicle and appearance processing;
- still throttles OCR to avoid starting an OCR process for every video frame.

Tune both modes in `config/default.yaml`.

## Detector strategy

### Face

1. OpenCV YuNet when its ONNX model is present.
2. OpenCV Haar cascade as a no-download fallback.
3. OpenCV SFace produces an anonymous embedding only when its model exists.

The event output distinguishes detection confidence from similarity. It never
claims identity.

### Person and vehicle

1. Ultralytics YOLO when installed.
2. OpenCV colour/contour heuristic for the synthetic integration fixture only.

Do not evaluate real CCTV performance with the heuristic fallback.

### Plate

1. Tested plate-specific YOLO weights when
   `models/license_plate_detector.pt` exists.
2. OpenCV Zoo LPD-YuNet plus contour candidates when its ONNX model exists.
3. OpenCV contour fallback for controlled high-contrast examples.
4. PaddleOCR when installed.
5. Tesseract fallback using multiple preprocessed variants and a restricted
   alphanumeric character set.

General COCO YOLO weights detect vehicles but not licence plates. A plate model
is a separate dependency. The official LPD-YuNet weights are trained on Chinese
plates; use them as an additional detector, not proof of South African accuracy.

## Output

```text
output/
|-- events/
|   `-- EVT-....json
|-- evidence/
|   `-- EVT-.../
|       |-- best_frame.jpg
|       |-- annotated_frame.jpg
|       |-- plate.jpg
|       `-- faces/
|           |-- face_0.jpg
|           `-- face_0_embedding.npy
`-- events.jsonl
```

Important event fields:

- `schema_version`;
- timezone-aware `timestamp`;
- model/fallback versions;
- separate plate detection and OCR confidence;
- `camera_trust_score` and its component metrics;
- evidence paths;
- honest `Unknown` or `null` values.

Generate the machine-readable event schema:

```powershell
python -c "from sentinel_camera_ai.schemas import CameraEvent; import json; print(json.dumps(CameraEvent.model_json_schema(), indent=2))" > schemas\event-v1.schema.json
```

## Camera Trust Score

Default weights:

```text
25% sharpness
20% lighting
30% detection confidence
15% unobstructed view
10% resolution
```

The result is a quality heuristic, not a scientific probability. Recalibrate
blur and lighting thresholds using the actual demo cameras.

## Repeat matching

Vehicle:

- normalized fuzzy plate comparison is strongest;
- colour and broad type support the result;
- missing plates produce a low-evidence warning.

Face:

- compares anonymous SFace vectors only when both exist;
- requires reasonable camera trust;
- reports similarity, not identity probability.

Appearance:

- compares upper/lower clothing colours;
- supports cap/backpack fields when a future attribute model supplies them.

Thresholds in the source are safe demo defaults. Calibrate them on labelled
same/different validation pairs before any real use.

## AWS publishing

Keep AI processing local for the first working demo. When ready:

1. configure IAM Identity Center and run `aws configure sso`;
2. configure the private bucket and Region in `config/default.yaml`;
3. set `aws.enabled: true`;
4. run `process ... --publish-aws`.

The adapter:

- uploads evidence privately to S3;
- stores the event JSON under the configured events prefix;
- optionally posts the same JSON to the team ingestion API;
- never reads access keys from this repository.

Do not make the evidence bucket public. The backend should issue temporary
presigned URLs to authenticated users.

## Docker

The base container includes OpenCV and Tesseract:

```powershell
docker compose build
docker compose run --rm camera-ai doctor
docker compose run --rm camera-ai
```

Install the YOLO extra and include authorized model weights in a production
image only after the local pipeline has passed.

## Tests

```powershell
python -m pytest
```

The integration test creates safe synthetic clips and verifies:

- YuNet face detection and SFace anonymous embedding on a fictional AI-generated face;
- plate detection and OCR;
- complete event JSON;
- trust scores;
- NORMAL/HEIGHTENED behavior;
- repeat vehicle matching across two cameras.

## Accuracy and safety boundaries

No computer-vision pipeline can be guaranteed never to fail. This project is
designed to fail visibly:

- uncertain values become `Unknown` or `null`;
- missing optional models select labelled fallbacks;
- model versions and fallback notes are recorded;
- important links remain candidates for human review;
- the system does not infer race, emotion, criminal intent or protected traits.

See `THIRD_PARTY_MODELS.md` before adding or distributing weights.
