from pathlib import Path

from fastapi.testclient import TestClient

from sentinel_ops.main import app
from sentinel_ops.member_mesh import initialise_member_store
from sentinel_ops.security_dispatch import initialise_security_store
from sentinel_ops.storage import connect


def _seed_active_incident() -> str:
    initialise_member_store()
    incident_id = "INC-SECURITY-DEMO"
    with connect() as db:
        db.execute(
            """
            INSERT INTO member_incidents(
                incident_id, profile_id, origin_user_id, origin_camera_id,
                origin_sighting_id, incident_type, status, started_at, updated_at,
                duration_minutes, expires_at, notes, confirmed_by
            ) VALUES (?, 'FACE-DEMO', 'USR-001', 'CAM-U1-01', 'SIGHT-DEMO',
                      'HOME_INVASION', 'ACTIVE', datetime('now'), datetime('now'),
                      30, datetime('now','+30 minutes'), 'Security routing test', 'Test operator')
            ON CONFLICT(incident_id) DO UPDATE SET status='ACTIVE'
            """,
            (incident_id,),
        )
    return incident_id


def test_security_operations_seed_and_route_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "security.db"))
    initialise_security_store()
    client = TestClient(app)

    state = client.get("/api/security/operations")
    assert state.status_code == 200
    payload = state.json()
    assert payload["pilot"] == "Benoni / Lakefield"
    assert len(payload["companies"]) == 3
    assert len(payload["units"]) == 6
    assert len(payload["hotspots"]) == 7
    assert payload["statistics"]["units_total"] == 6
    assert "claim amounts" in payload["privacy_note"]

    preview = client.get("/api/security/units/LRF-12/route-preview?max_stops=4")
    assert preview.status_code == 200
    route = preview.json()
    assert route["route_kind"] == "OPTIMISED_PATROL"
    assert route["distance_km"] > 0
    assert route["estimated_fuel_litres"] > 0
    assert route["coverage_percent"] > 0
    assert len(route["hotspot_ids"]) == 4
    assert "overlap" in route["method"].lower()


def test_member_incident_creates_dispatch_notifications_and_movement(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "dispatch.db"))
    initialise_security_store()
    incident_id = _seed_active_incident()
    client = TestClient(app)

    created = client.post("/api/security/dispatch/from-latest-member")
    assert created.status_code == 200
    dispatch = created.json()
    assert dispatch["member_incident_id"] == incident_id
    assert dispatch["status"] == "AWAITING_ACKNOWLEDGEMENT"
    assert dispatch["selected_unit_id"]
    assert len(dispatch["backup_unit_ids"]) == 2
    assert len(dispatch["notifications"]) == 3
    assert dispatch["address"].startswith("17 Sher Avenue")
    assert "claim" not in dispatch

    state_before = client.get("/api/security/operations").json()
    selected = next(u for u in state_before["units"] if u["unit_id"] == dispatch["selected_unit_id"])
    before = (selected["latitude"], selected["longitude"])

    acknowledged = client.post(
        f"/api/security/dispatches/{dispatch['dispatch_id']}/acknowledge",
        json={"acknowledged_by": "Test control room"},
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"

    tick = client.post("/api/security/simulation/tick", json={"steps": 1})
    assert tick.status_code == 200
    assert tick.json()["count"] == 6

    state_after = client.get("/api/security/operations").json()
    selected_after = next(u for u in state_after["units"] if u["unit_id"] == dispatch["selected_unit_id"])
    after = (selected_after["latitude"], selected_after["longitude"])
    assert after != before

    notification_id = dispatch["notifications"][0]["notification_id"]
    sent = client.post(
        f"/api/security/notifications/{notification_id}/simulate-send",
        json={"sent_by": "Test control room"},
    )
    assert sent.status_code == 200
    assert sent.json()["status"] == "DELIVERED_DEMO"

    database = client.get("/api/security/database")
    assert database.status_code == 200
    db = database.json()
    assert db["tables"]["security_dispatches"] == 1
    assert db["tables"]["security_notifications"] == 3
    assert db["aws_outbox_pending"] > 0
