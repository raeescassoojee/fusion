from pathlib import Path

from fastapi.testclient import TestClient

import sentinel_ops.security_dispatch as security_dispatch_module
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
    assert dispatch["address"].startswith("10 Ness Avenue")
    assert "claim" not in dispatch

    # Simulate a dispatch saved by the old Killarney frontend. Reading the live
    # operations state must repair its address, destination and response route.
    with connect() as db:
        db.execute(
            """
            UPDATE security_dispatches
            SET address='10 Killarney Avenue, Lakefield',
                latitude=-26.184775, longitude=28.289372,
                route_json='[{"latitude":-26.184775,"longitude":28.289372,"kind":"INCIDENT"}]'
            WHERE dispatch_id=?
            """,
            (dispatch["dispatch_id"],),
        )
    repaired_state = client.get("/api/security/operations")
    assert repaired_state.status_code == 200
    repaired = next(item for item in repaired_state.json()["dispatches"] if item["dispatch_id"] == dispatch["dispatch_id"])
    assert repaired["address"] == "10 Ness Avenue, Lakefield"
    assert repaired["latitude"] == -26.18119
    assert repaired["longitude"] == 28.28998
    assert repaired["route"][-1]["kind"] == "INCIDENT"
    assert repaired["route"][-1]["latitude"] == repaired["latitude"]
    assert repaired["route"][-1]["longitude"] == repaired["longitude"]

    response_route = client.get(
        f"/api/security/units/{dispatch['selected_unit_id']}/route-preview",
        params={"max_stops": 4, "dispatch_id": dispatch["dispatch_id"]},
    )
    assert response_route.status_code == 200
    route = response_route.json()
    assert route["route_kind"] == "INCIDENT_RESPONSE"
    assert route["hotspot_ids"] == []
    assert route["points"][-1]["kind"] == "INCIDENT"
    assert route["points"][-1]["latitude"] == repaired["latitude"]
    assert route["points"][-1]["longitude"] == repaired["longitude"]
    assert not any(point["kind"] == "HOTSPOT" for point in route["points"])

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


def test_control_room_test_alert_creates_member_incident_and_dispatch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "test-alert.db"))
    client = TestClient(app)
    initialise_security_store()
    _seed_active_incident()

    created = client.post("/api/security/dispatch/test-alert")
    assert created.status_code == 200
    payload = created.json()
    assert payload["origin_household"]
    assert payload["status"] == "AWAITING_ACKNOWLEDGEMENT"
    assert payload["dispatch_id"]
    assert payload["selected_unit_id"]

    assert client.get("/api/member/mesh-state").status_code == 200


def test_control_room_test_alert_works_without_prior_camera_sighting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "empty-test-alert.db"))
    client = TestClient(app)

    created = client.post("/api/security/dispatch/test-alert")

    assert created.status_code == 200
    payload = created.json()
    assert payload["origin_household"] == "10 Ness Avenue"
    assert payload["status"] == "AWAITING_ACKNOWLEDGEMENT"
    assert payload["dispatch_id"]
    assert payload["selected_unit_id"]

    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM face_sightings").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM member_incidents").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM security_dispatches").fetchone()[0] == 1


def test_test_alert_auto_sends_one_free_service_message(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "auto-whatsapp.db"))
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_AUTO_SEND_FREE_ONLY", True)
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_AUTO_SEND_DAILY_LIMIT", 10)
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_MESSAGE_TEXT", "RESPONSE IS ON THE WAY")
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_PHONE_NUMBER_ID", "test-phone-id")
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(security_dispatch_module, "WHATSAPP_RECIPIENT", "27826502010")

    sent: list[str] = []

    def fake_send(notification, dispatch):
        sent.append(notification["notification_id"])
        return {
            "provider": "Meta WhatsApp Cloud API",
            "message_id": "wamid.test",
            "recipient": "+27826502010",
            "template": None,
            "message_type": "text",
            "accepted": True,
            "raw": {"messages": [{"id": "wamid.test"}]},
        }

    monkeypatch.setattr(security_dispatch_module, "_send_whatsapp_template", fake_send)
    client = TestClient(app)
    initialise_security_store()
    _seed_active_incident()
    created = client.post("/api/security/dispatch/test-alert")

    assert created.status_code == 200
    payload = created.json()
    assert payload["auto_whatsapp"]["status"] == "SENT_TO_PROVIDER"
    assert payload["auto_whatsapp"]["billing_guard"] == "SERVICE_TEXT_ONLY"
    assert len(sent) == 1

    with connect() as db:
        statuses = [row["status"] for row in db.execute(
            "SELECT status FROM security_notifications WHERE dispatch_id=? ORDER BY created_at",
            (payload["dispatch_id"],),
        ).fetchall()]
        auto_events = db.execute(
            "SELECT COUNT(*) FROM security_activity WHERE event_type='WHATSAPP_AUTO_SENT'"
        ).fetchone()[0]
    assert statuses.count("SENT_TO_PROVIDER") == 1
    assert statuses.count("QUEUED_LOCAL") == 2
    assert auto_events == 1
