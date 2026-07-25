"""Camera intake router: upload real footage, run the sentinel-camera-ai pipeline,
serve per-house camera roster and evidence media.

Wire into services/operations/src/sentinel_ops/main.py with:

    from sentinel_ops.camera_upload import router as camera_router
    app.include_router(camera_router)

Requires the camera AI package on PYTHONPATH (repo `src/`) and its deps
(opencv-python, numpy). If the pipeline import fails the router still serves the
camera roster and returns a clear 503 on upload rather than crashing the app.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from sentinel_ops.camera_bridge import camera_ai_to_operations
from sentinel_ops.claims_bridge import load_claims_hotspots
from sentinel_ops.ingestion import ingest_event

router = APIRouter(tags=["cameras"])

OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = OPERATIONS_ROOT.parents[1]
UPLOAD_ROOT = OPERATIONS_ROOT / "uploads"
EVIDENCE_ROOT = UPLOAD_ROOT / "evidence"
CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_UPLOAD_BYTES = int(os.getenv("SENTINEL_MAX_UPLOAD_BYTES", str(150 * 1024 * 1024)))

# ---------------------------------------------------------------- camera roster
# Participating "ring-style" doorbell cameras, one per member property, spread
# across pilot suburbs. Coordinates are offset from their hotspot centre so a
# street of cameras sits inside the geofence it belongs to.
CAMERA_ROSTER: list[dict[str, Any]] = [
    {"camera_id": "CAM-BRY-01", "household": "14 Hillcrest Ave", "suburb": "Bryanston",
     "metro": "Gauteng", "latitude": -26.0514, "longitude": 28.0281},
    {"camera_id": "CAM-BRY-02", "household": "22 Kei Rd", "suburb": "Bryanston",
     "metro": "Gauteng", "latitude": -26.0538, "longitude": 28.0309},
    {"camera_id": "CAM-FOU-01", "household": "8 Montrose Ave", "suburb": "Fourways",
     "metro": "Gauteng", "latitude": -26.0186, "longitude": 28.0104},
    {"camera_id": "CAM-GAR-01", "household": "51 Rooibok St", "suburb": "Garsfontein",
     "metro": "Gauteng", "latitude": -25.8110, "longitude": 28.2960},
    {"camera_id": "CAM-MEN-01", "household": "3 Dely Rd", "suburb": "Menlo Park",
     "metro": "Gauteng", "latitude": -25.7797, "longitude": 28.2611},
    {"camera_id": "CAM-BED-01", "household": "17 Van Buuren Rd", "suburb": "Bedfordview",
     "metro": "Gauteng", "latitude": -26.1795, "longitude": 28.1345},
    {"camera_id": "CAM-SEA-01", "household": "9 Marine Dr", "suburb": "Sea Point",
     "metro": "Cape Town", "latitude": -33.9172, "longitude": 18.3922},
    {"camera_id": "CAM-RON-01", "household": "40 Campground Rd", "suburb": "Rondebosch",
     "metro": "Cape Town", "latitude": -33.9681, "longitude": 18.4878},
]


def _hotspot_modes(metro: str | None) -> dict[str, dict[str, Any]]:
    """Map suburb name -> {hotspot_id, mode, risk} so cameras inherit Adaptive Edge
    mode from the claims risk of the geofence they sit in."""
    out: dict[str, dict[str, Any]] = {}
    for target in ({metro} if metro else {"Gauteng", "Cape Town"}):
        try:
            hotspots, _ = load_claims_hotspots(target)
        except Exception:
            continue
        for h in hotspots:
            risk = getattr(h, "operational_priority", None) or getattr(h, "risk_score", 0) or 0
            out[h.name.strip().lower()] = {
                "hotspot_id": h.hotspot_id,
                "risk": round(float(risk), 1),
                "mode": "HEIGHTENED" if float(risk) >= 60 else "NORMAL",
            }
    return out


@router.get("/api/cameras")
def list_cameras(metro: str | None = None):
    """Per-household participating cameras, with the mode their geofence risk implies."""
    modes = _hotspot_modes(metro)
    cameras = []
    for cam in CAMERA_ROSTER:
        if metro and cam["metro"] != metro:
            continue
        ctx = modes.get(cam["suburb"].strip().lower(), {})
        cameras.append({
            **cam,
            "hotspot_id": ctx.get("hotspot_id"),
            "mode": ctx.get("mode", "NORMAL"),
            "geofence_risk": ctx.get("risk"),
            "status": "ONLINE",
        })
    return {"metro": metro, "count": len(cameras), "cameras": cameras}


def _pipeline(output_dir: Path):
    try:
        from sentinel_camera_ai.config import AppConfig
        from sentinel_camera_ai.pipeline import CameraAIPipeline
    except Exception as exc:  # pragma: no cover - depends on deployment
        raise HTTPException(
            status_code=503,
            detail=f"camera AI pipeline unavailable: {exc}. Add repo src/ to PYTHONPATH.",
        ) from exc
    config = AppConfig.load(CONFIG_PATH)
    config.output_dir = str(output_dir)
    return CameraAIPipeline(config)


@router.post("/api/cameras/upload")
async def upload_footage(
    file: UploadFile = File(...),
    camera_id: str = Form("CAM-UPLOAD"),
    latitude: float = Form(-26.0514),
    longitude: float = Form(28.0281),
    mode: str = Form("HEIGHTENED"),
    ingest: bool = Form(True),
    publish_aws: bool = Form(False),
):
    """Run real uploaded footage through the camera-AI pipeline and return event-v1 JSON.

    Accepts video or image. Each detected candidate becomes one event, is optionally
    ingested into the operations store, and its evidence crops are exposed through
    /api/cameras/media/{event_id}/{name}.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {suffix!r}; "
                   f"videos={sorted(VIDEO_SUFFIXES)} images={sorted(IMAGE_SUFFIXES)}",
        )

    batch = uuid.uuid4().hex[:10]
    out_dir = UPLOAD_ROOT / batch
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.gettempdir()) / f"sentinel-{batch}{suffix}"
    with tmp.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    size_bytes = tmp.stat().st_size
    if size_bytes > MAX_UPLOAD_BYTES:
        tmp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=(
                f"upload is {size_bytes / 1024 / 1024:.1f} MB; "
                f"demo limit is {MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MB"
            ),
        )

    try:
        pipeline = _pipeline(out_dir)
        results = pipeline.process_media(
            input_path=tmp,
            camera_id=camera_id,
            latitude=latitude,
            longitude=longitude,
            mode=mode.upper(),
            start_timestamp=datetime.now().astimezone(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"pipeline failed: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)

    events: list[dict[str, Any]] = []
    ingested = 0
    ingest_errors: list[dict[str, str]] = []
    aws_published = 0
    aws_errors: list[dict[str, str]] = []
    aws_sink = None
    aws_requested = publish_aws or bool(os.getenv("SENTINEL_EVIDENCE_BUCKET"))
    if aws_requested:
        try:
            from sentinel_camera_ai.aws_sink import AwsSink

            pipeline.config.aws.enabled = True
            aws_sink = AwsSink(pipeline.config.aws)
        except Exception as exc:
            # AWS is an optional enhancement. A credential/network issue must never
            # destroy the local judging demo. Report it clearly and continue locally.
            aws_errors.append({"event_id": "setup", "error": str(exc)})
    for event, event_path in results:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        media_base = f"/api/cameras/media/{batch}"
        payload["source_media"] = file.filename
        payload["_batch"] = batch
        payload["_media_base"] = media_base

        # The camera pipeline writes paths relative to its batch output directory.
        # Keep those relative paths in the upload response for the existing UI, but
        # store an API-addressable URL in the operations event so Rewind links work.
        operations_payload = dict(payload)
        if operations_payload.get("media_url"):
            operations_payload["media_url"] = (
                f"{media_base}/{str(operations_payload['media_url']).lstrip('/')}"
            )

        if ingest:
            try:
                ingest_event(camera_ai_to_operations(operations_payload))
                ingested += 1
            except Exception as exc:
                ingest_errors.append({
                    "event_id": str(payload.get("event_id", "unknown")),
                    "error": str(exc),
                })

        if aws_sink is not None:
            try:
                published = aws_sink.publish(event, out_dir)
                payload["_aws"] = {
                    "event_object": published.event_object,
                    "evidence_objects": published.evidence_objects,
                    "api_status": published.api_status,
                }
                aws_published += 1
            except Exception as exc:
                aws_errors.append({
                    "event_id": str(payload.get("event_id", "unknown")),
                    "error": str(exc),
                })
        events.append(payload)

    return {
        "batch": batch,
        "camera_id": camera_id,
        "source_filename": file.filename,
        "event_count": len(events),
        "ingested": ingested,
        "ingest_errors": ingest_errors,
        "aws": {
            "requested": aws_requested,
            "published": aws_published,
            "errors": aws_errors,
            "bucket": os.getenv("SENTINEL_EVIDENCE_BUCKET") or None,
        },
        "events": events,
        "note": "Events produced by sentinel-camera-ai from the uploaded media.",
    }


@router.post("/api/vision/rekognition/{event_id}")
def rekognition_event(event_id: str):
    """Corroborate a stored evidence crop with Amazon Rekognition DetectFaces.

    Returns available=False with a reason when AWS is not configured, so the
    local YuNet result remains the source of truth and the demo never breaks.
    """
    from sentinel_ops.rekognition import detect_faces_in_file

    safe_id = "".join(c for c in event_id if c.isalnum() or c in "-_")
    if not safe_id:
        raise HTTPException(status_code=400, detail="invalid event id")

    matches = sorted(EVIDENCE_ROOT.glob(f"{safe_id}/*.jpg")) or sorted(
        UPLOAD_ROOT.glob(f"*/evidence/{safe_id}/*.jpg")
    )
    if not matches:
        raise HTTPException(
            status_code=404, detail=f"no stored evidence image for {safe_id}"
        )

    crop = matches[0]
    return {
        "event_id": safe_id,
        "image": crop.name,
        "rekognition": detect_faces_in_file(crop),
    }


@router.get("/api/cameras/media/{batch}/{path:path}", include_in_schema=False)
def camera_media(batch: str, path: str):
    """Serve evidence crops produced for an uploaded batch."""
    root = (UPLOAD_ROOT / batch).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="media not found")
    return FileResponse(target)
