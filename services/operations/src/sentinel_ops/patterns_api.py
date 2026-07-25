"""Height estimation and evidence-pattern endpoints.

Wire into main.py:

    from sentinel_ops.patterns_api import router as patterns_router
    app.include_router(patterns_router)

Requires `height.py` and `patterns.py` importable (drop them beside this file).
Uses DynamoDB when AWS is reachable and a local SQLite mirror otherwise, so the
demo never depends on the venue network.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from sentinel_ops.height import (
    CameraCalibration,
    HeightUnavailable,
    estimate_height,
    observations_from_person_boxes,
)
from sentinel_ops.patterns import (
    PatternRegistry,
    Signature,
    score_pair,
    get_store,
)

router = APIRouter(tags=["patterns"])

_registry: PatternRegistry | None = None
_calibrations: dict[str, CameraCalibration] = {}


def registry() -> PatternRegistry:
    global _registry
    if _registry is None:
        _registry = PatternRegistry(get_store())
    return _registry


# ------------------------------------------------------------------ calibration
class CalibrationIn(BaseModel):
    camera_id: str
    image_width: int
    image_height: int
    mode: Literal["INTRINSIC", "REFERENCE"] = "INTRINSIC"
    mount_height_m: float | None = None
    tilt_deg: float | None = None
    horizontal_fov_deg: float | None = None
    horizon_y: float | None = None
    ref_height_m: float | None = None
    ref_foot_y: float | None = None
    ref_head_y: float | None = None
    calibration_score: float = 100.0


@router.put("/api/cameras/{camera_id}/calibration")
def set_calibration(camera_id: str, body: CalibrationIn):
    """Store the geometry a camera needs before it may estimate height."""
    data = body.model_dump()
    data["camera_id"] = camera_id
    cal = CameraCalibration(**data)
    try:
        cal.validate()
    except HeightUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _calibrations[camera_id] = cal
    return {"camera_id": camera_id, "mode": cal.mode, "status": "calibrated"}


@router.get("/api/cameras/{camera_id}/calibration")
def get_calibration(camera_id: str):
    cal = _calibrations.get(camera_id)
    if not cal:
        raise HTTPException(status_code=404, detail=f"{camera_id} has no calibration on file")
    return cal.__dict__


class HeightIn(BaseModel):
    camera_id: str
    person_boxes: list[dict[str, float]] = Field(
        ..., description="detector person boxes across frames: x, y, width, height"
    )
    image_width: int
    image_height: int


@router.post("/api/height/estimate")
def height_estimate(body: HeightIn):
    """Estimate a height band from tracked person boxes.

    Returns 409 with an explanation when the estimate would not be defensible —
    a refusal is a valid, useful answer here.
    """
    cal = _calibrations.get(body.camera_id)
    if not cal:
        raise HTTPException(
            status_code=404,
            detail=f"{body.camera_id} is not calibrated — PUT its calibration first",
        )
    obs = observations_from_person_boxes(body.person_boxes, body.image_height, body.image_width)
    try:
        est = estimate_height(cal, obs)
    except HeightUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return est.to_dict()


# ------------------------------------------------------------------ patterns
class SignatureIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event: dict[str, Any]
    height: dict[str, Any] | None = None
    geofence_id: str | None = None
    nearby_geofences: list[str] = Field(default_factory=list)
    should_register: bool = Field(
        default=True,
        validation_alias=AliasChoices("register", "register_pattern", "should_register"),
        serialization_alias="register",
    )


@router.post("/api/patterns/ingest")
def ingest(body: SignatureIn):
    """Turn a camera event into signatures, match them, and cluster recurrences.

    Nothing here identifies a person. Matches are POSSIBLE_* candidates that a
    human must confirm before they carry any weight.
    """
    reg = registry()
    try:
        sigs = reg.signature_from_event(body.event, body.height, body.geofence_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"event missing field: {exc}") from exc

    out = []
    for sig in sigs:
        matches = reg.match(sig, nearby_geofences=body.nearby_geofences)
        pattern = reg.register(sig, matches) if body.should_register else None
        out.append({
            "signature_id": sig.signature_id,
            "kind": sig.kind,
            "cues": sig.cues,
            "matches": [m.__dict__ for m in matches],
            "pattern": pattern.__dict__ | {"description": pattern.describe()} if pattern else None,
        })
    return {"event_id": body.event.get("event_id"), "signatures": out}


@router.get("/api/patterns")
def list_patterns(status: str | None = None):
    reg = registry()
    pats = reg.store.list_patterns(status)
    return {
        "count": len(pats),
        "stats": reg.stats(),
        "patterns": [p.__dict__ | {"description": p.describe()} for p in pats],
    }


@router.get("/api/patterns/{pattern_id}")
def get_pattern(pattern_id: str):
    reg = registry()
    p = reg.store.get_pattern(pattern_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"unknown pattern {pattern_id}")
    return {
        "pattern": p.__dict__ | {"description": p.describe()},
        "reviews": reg.store.list_reviews(pattern_id),
    }


class ReviewIn(BaseModel):
    decision: Literal["CONFIRMED_BY_REVIEW", "DISMISSED"]
    reason: str
    reviewer: str
    saps_reference: str | None = None
    claim_ref: str | None = None


@router.post("/api/patterns/{pattern_id}/review")
def review_pattern(pattern_id: str, body: ReviewIn):
    """Record a human decision. A written reason is mandatory."""
    try:
        p = registry().review(
            pattern_id, body.decision, body.reason, body.reviewer,
            body.saps_reference, body.claim_ref,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return p.__dict__ | {"description": p.describe()}


class ComparePair(BaseModel):
    first: dict[str, Any]
    second: dict[str, Any]


@router.post("/api/patterns/compare")
def compare(body: ComparePair):
    """Score two signatures directly, without storing anything."""
    try:
        a, b = Signature(**body.first), Signature(**body.second)
        return score_pair(a, b).__dict__
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
