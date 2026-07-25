from datetime import datetime

from sentinel_camera_ai.matching import compare_events, normalize_plate
from sentinel_camera_ai.schemas import (
    AppearanceEvidence,
    CameraEvent,
    Location,
    PlateEvidence,
    QualityMetrics,
    VehicleEvidence,
)


def _event(event_id: str, plate: str, colour: str = "White") -> CameraEvent:
    return CameraEvent(
        event_id=event_id,
        camera_id="CAM01",
        timestamp=datetime.fromisoformat("2026-07-24T21:07:00+02:00"),
        mode="HEIGHTENED",
        location=Location(latitude=-25.7, longitude=28.3),
        source_media="fixture.jpg",
        media_url="evidence/fixture.jpg",
        plate=PlateEvidence(text=plate, ocr_confidence=0.9),
        vehicle=VehicleEvidence(colour=colour, type="Car"),
        appearance=AppearanceEvidence(upper_colour="Black", lower_colour="Blue"),
        camera_trust_score=80,
        quality_metrics=QualityMetrics(
            sharpness=80,
            lighting=80,
            detection=80,
            unobstructed=80,
            resolution=80,
        ),
    )


def test_plate_normalization():
    assert normalize_plate(" ab-12 cd gp ") == "AB12CDGP"


def test_same_vehicle_and_appearance():
    result = compare_events(_event("A", "AB 12 CD GP"), _event("B", "AB12CDGP"))
    assert result.possible_same_vehicle.value is True
    assert result.possible_same_vehicle.score > 0.95
    assert result.possible_same_appearance.value is True


def test_different_plate_reduces_vehicle_score():
    result = compare_events(_event("A", "AB12CDGP"), _event("B", "ZZ99ZZGP"))
    assert result.possible_same_vehicle.value is False


def test_unknown_appearance_is_not_a_match():
    first = _event("A", "AB12CDGP")
    second = _event("B", "AB12CDGP")
    first.appearance = AppearanceEvidence()
    second.appearance = AppearanceEvidence()
    result = compare_events(first, second)
    assert result.possible_same_appearance.value is False
    assert result.possible_same_appearance.score == 0
    assert result.possible_same_appearance.evidence_strength == "NONE"
