# Live face-camera demo

This build links the browser camera to the existing Live AI workspace.

## What changed

- The user can select a physical webcam/phone camera from a device dropdown.
- The default camera is the selfie/front-facing camera for a face demo.
- BlazeFace detection draws a bright green tracking box that follows every detected face.
- The label changes from `FACE DETECTED` to `FACE TRACKED · CAPTURING` once the face is stable.
- Stable face tracks automatically create a consented demo event at most once every six seconds.
- Manual `Capture event` remains available.
- Captured event count is displayed live.
- A green flash confirms that an event was captured.
- The captured event appears in the Evidence event store and can be pushed to the operations backend.

This is live face detection and tracking. It does not claim to identify a person by name.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = ".\services\operations\src;.\src"
python -m uvicorn sentinel_ops.main:app --reload
```

Open:

`http://127.0.0.1:8000/dashboard`

Then:

1. Choose `Discovery member`.
2. Open `Live AI`.
3. Select the physical camera or leave `Default / selfie camera`.
4. Press `Start face-tracking demo`.
5. Allow browser camera access.
6. Keep your face visible for a few frames.
7. The box turns green and follows your face.
8. A stable face creates a demo capture, shown by the green flash and capture counter.
9. Open `Evidence` to view the resulting event.

## Requirements

- Use Chrome or Edge.
- Open from `localhost`; do not double-click the HTML file directly.
- Internet is needed to load TensorFlow.js, COCO-SSD and BlazeFace from the current CDN setup.
- Use only your own face or another consenting participant.
