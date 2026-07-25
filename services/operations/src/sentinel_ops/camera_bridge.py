from __future__ import annotations

from typing import Any

from sentinel_ops.models import (
    AppearanceSignal,
    CameraEvent,
    FaceSignal,
    Location,
    PlateSignal,
    VehicleSignal,
)


def _confidence(*values: Any) -> float:
    numbers = []
    for value in values:
        if isinstance(value, (int, float)):
            numbers.append(float(value))
    if not numbers:
        return 0.0
    return max(0.0, min(1.0, max(numbers)))


def camera_ai_to_operations(payload: dict[str, Any]) -> CameraEvent:
    """Translate Person 3's event-v1 payload into the operations event contract."""
    plate = payload.get("plate") or {}
    face = payload.get("face") or {}
    vehicle = payload.get("vehicle") or {}
    appearance = payload.get("appearance") or {}
    location = payload.get("location") or {}

    descriptor_parts = [
        str(appearance.get("upper_colour") or ""),
        str(appearance.get("lower_colour") or ""),
        f"cap={appearance.get('cap')}",
        f"backpack={appearance.get('backpack')}",
    ]
    descriptor = "|".join(part for part in descriptor_parts if part)

    make_model = vehicle.get("make_model")
    return CameraEvent(
        event_id=str(payload["event_id"]),
        camera_id=str(payload["camera_id"]),
        timestamp=payload["timestamp"],
        location=Location(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
        ),
        media_url=payload.get("media_url"),
        plate=PlateSignal(
            text=plate.get("text"),
            confidence=_confidence(
                plate.get("ocr_confidence"),
                plate.get("detection_confidence"),
            ),
        ),
        face=FaceSignal(
            reference_token=face.get("embedding_ref"),
            embedding=payload.get("face_embedding"),
            confidence=_confidence(face.get("detection_confidence")),
        ),
        vehicle=VehicleSignal(
            colour=vehicle.get("colour"),
            type=vehicle.get("type"),
            model=make_model,
        ),
        appearance=AppearanceSignal(
            upper_colour=appearance.get("upper_colour"),
            lower_colour=appearance.get("lower_colour"),
            descriptor_token=descriptor or None,
        ),
        camera_trust_score=float(payload.get("camera_trust_score", 50)),
        source="sentinel-camera-ai",
    )
