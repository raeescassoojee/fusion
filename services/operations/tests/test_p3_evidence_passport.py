from __future__ import annotations

from fastapi.testclient import TestClient

from sentinel_ops.main import app


def test_camera_event_passport_verifies_integrity(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "p3.db"))
    with TestClient(app) as client:
        seeded = client.post("/api/demo/seed")
        assert seeded.status_code == 200
        event_id = client.get("/api/events").json()[0]["event_id"]
        response = client.get(f"/api/evidence/passports/camera-event/{event_id}")

    assert response.status_code == 200
    passport = response.json()
    assert passport["evidence_id"] == event_id
    assert passport["integrity"]["algorithm"] == "SHA-256"
    assert passport["integrity"]["verified"] is True
    assert len(passport["integrity"]["evidence_sha256"]) == 64
    assert passport["privacy"]["identity_asserted"] is False
    assert passport["trust"]["human_review_required"] is True
    assert passport["retention"]["enforcement"] == "DEMO_METADATA_ONLY"


def test_client_latency_is_measured_and_whitelisted(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "latency.db"))
    with TestClient(app) as client:
        client.delete("/api/performance")
        recorded = client.post(
            "/api/performance/record",
            json={"name": "identity_roundtrip_ms", "milliseconds": 432.1},
        )
        rejected = client.post(
            "/api/performance/record",
            json={"name": "made_up_latency", "milliseconds": 1},
        )
        snapshot = client.get("/api/performance").json()

    assert recorded.status_code == 200
    assert rejected.status_code == 422
    metric = snapshot["metrics"]["identity_roundtrip_ms"]
    assert metric["runs"] == 1
    assert metric["latest_ms"] == 432.1


def test_dashboard_exposes_p3_controls():
    with TestClient(app) as client:
        html = client.get("/dashboard").text

    assert "Live measured latency" in html
    assert "Evidence Passport" in html
    assert "dataset.evidencePassport" in html
    assert "/api/performance/record" in html
