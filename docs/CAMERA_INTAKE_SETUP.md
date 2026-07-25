# Camera intake — setup

Two files:

| File | Goes to |
|---|---|
| `dashboard.html` | `services/operations/static/dashboard.html` (replace) |
| `camera_upload.py` | `services/operations/src/sentinel_ops/camera_upload.py` (new) |

## Wire the router

In `services/operations/src/sentinel_ops/main.py`, add the import beside the other
`sentinel_ops` imports and include the router after `app` is created:

```python
from sentinel_ops.camera_upload import router as camera_router
...
app.include_router(camera_router)
```

## Dependencies

```powershell
pip install python-multipart rapidfuzz
```

`python-multipart` is required for file uploads; `rapidfuzz` is used by the plate
matcher inside `sentinel_camera_ai`. Everything else is already in the repo
requirements.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = ".\services\operations\src;.\src"
python -m uvicorn sentinel_ops.main:app --reload
```

Open `http://127.0.0.1:8000/dashboard`.

## New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/cameras?metro=` | Participating households, each with the Adaptive Edge mode implied by its geofence risk |
| `POST /api/cameras/upload` | Runs uploaded video/image through `sentinel_camera_ai`, returns event-v1 JSON, ingests into the operations store |
| `GET /api/cameras/media/{batch}/{path}` | Serves evidence crops produced by an upload |

Upload accepts `file`, `camera_id`, `latitude`, `longitude`, `mode`, `ingest`.
Output lands in `services/operations/uploads/{batch}/` — add that to `.gitignore`.

Verified locally: a 90-frame clip produced 2 events, plate `AB12CDGP` at 0.91 OCR
confidence, trust 78 and 69, both ingested and their frames served over HTTP.

## Where the AI runs

- **Browser** (no backend needed): COCO-SSD object detection and BlazeFace face
  detection on live webcam and uploaded footage, plus appearance-colour extraction
  and a multi-signal comparison with journey plausibility.
- **Server**: the full Python stack — licence-plate detection with OCR, YuNet faces,
  vehicle colour/type, quality-weighted trust scoring.

The browser layer never claims to do plate OCR; the UI says so explicitly and routes
that work to the server pipeline.

## Camera roster

`CAMERA_ROSTER` at the top of `camera_upload.py` is the list of participating
households. Add real addresses and coordinates there; suburb names are matched
against hotspot names to inherit risk and mode, so the suburb string must match the
claims pipeline's area name.
