from sentinel_ops.ingestion import ingest_claim, ingest_event
from sentinel_ops.models import CameraEvent, Claim
from sentinel_ops.storage import status


def _event(event_id: str, longitude: float) -> CameraEvent:
    return CameraEvent.model_validate(
        {
            "event_id": event_id,
            "camera_id": f"CAM-{event_id}",
            "timestamp": "2026-07-24T13:00:00+02:00",
            "location": {
                "latitude": -26.0514,
                "longitude": longitude,
            },
            "plate": {"text": "AB12CDGP", "confidence": 0.95},
            "face": {
                "reference_token": "CONSENTED_DEMO",
                "embedding": [0.8, 0.1, 0.3],
                "confidence": 0.8,
            },
            "vehicle": {"colour": "white", "type": "sedan"},
            "appearance": {
                "upper_colour": "black",
                "lower_colour": "blue",
            },
            "camera_trust_score": 85,
            "source": "test",
        }
    )


def test_incoming_events_and_claim_are_stored(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SENTINEL_DATABASE_PATH",
        str(tmp_path / "sentinel_test.db"),
    )

    first = ingest_event(_event("E1", 28.0281))
    second = ingest_event(_event("E2", 28.0290))
    assert first["event"]["event_id"] == "E1"
    assert second["candidate_links"]

    claim = Claim.model_validate(
        {
            "claim_id": "C1",
            "incident_time": "2026-07-24T13:05:00+02:00",
            "location": {"latitude": -26.0514, "longitude": 28.0281},
            "claim_type": "Vehicle Theft",
            "claim_amount": 420000,
            "plate_text": "AB12CDGP",
            "vehicle_colour": "white",
            "vehicle_type": "sedan",
        }
    )
    result = ingest_claim(claim)
    assert len(result["timeline"]["items"]) == 2

    counts = status()
    assert counts["events"] == 2
    assert counts["claims"] == 1
