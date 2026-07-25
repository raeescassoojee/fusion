from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sentinel_ops.main import app


REPO_ROOT = Path(__file__).resolve().parents[3]
FACE_FIXTURE = REPO_ROOT / "media" / "synthetic_face_fixture.png"


def test_three_demo_members_and_persistent_cameras(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "member-demo.db"))
    client = TestClient(app)

    members = client.get("/api/members")
    assert members.status_code == 200
    payload = members.json()
    assert [user["household"] for user in payload["users"]] == [
        "17 Sher Avenue",
        "18 Sher Avenue",
        "19 Sher Avenue",
    ]

    cameras = client.get("/api/member/USR-001/cameras")
    assert cameras.status_code == 200
    assert cameras.json()["count"] == 1
    assert cameras.json()["cameras"][0]["camera_id"] == "CAM-U1-01"
    assert cameras.json()["cameras"][0]["household"] == "17 Sher Avenue"


def test_offline_demo_geocoder(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "geocode-demo.db"))
    client = TestClient(app)

    response = client.get("/api/member/geocode", params={"q": "18 Sher Avenue"})
    assert response.status_code == 200
    data = response.json()
    assert data["suburb"] == "Lakefield"
    assert data["latitude"] == -26.19809
    assert data["longitude"] == 28.31042


def test_local_detector_repeat_match_and_incident_watch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "mesh-demo.db"))
    client = TestClient(app)
    image = FACE_FIXTURE.read_bytes()

    detected = client.post(
        "/api/member/face-detect",
        files={"image": ("face.png", image, "image/png")},
    )
    assert detected.status_code == 200
    assert detected.json()["faces"]
    assert "OpenCV" in detected.json()["detector"]

    first = client.post(
        "/api/member/face-sightings",
        files={"image": ("face.png", image, "image/png")},
        data={"user_id": "USR-001", "camera_id": "CAM-U1-01", "browser_confidence": "0.99"},
    )
    assert first.status_code == 200
    assert first.json()["classification"] == "NEW_VISITOR"

    watch = client.post(
        "/api/member/incidents/start",
        json={
            "sighting_id": first.json()["sighting_id"],
            "incident_type": "DEMO_INTRUSION",
            "confirmed_by_operator": True,
        },
    )
    assert watch.status_code == 200
    statuses = {item["household"]: item["status"] for item in watch.json()["incident"]["notifications"]}
    assert statuses["17 Sher Avenue"] == "ORIGIN_CONFIRMED"
    assert statuses["18 Sher Avenue"] == "WATCH_ACTIVE"
    assert statuses["19 Sher Avenue"] == "WATCH_ACTIVE"

    second = client.post(
        "/api/member/face-sightings",
        files={"image": ("face.png", image, "image/png")},
        data={"user_id": "USR-002", "camera_id": "CAM-U2-01", "browser_confidence": "0.99"},
    )
    assert second.status_code == 200
    assert second.json()["classification"] == "REPEAT_VISITOR_CANDIDATE"
    assert second.json()["incident_watch"] is not None

    mesh = client.get("/api/member/mesh-state")
    assert mesh.status_code == 200
    assert mesh.json()["camera_count"] == 3
    assert mesh.json()["trail_count"] == 1
    notifications = mesh.json()["active_incidents"][0]["notifications"]
    status_by_house = {item["household"]: item["status"] for item in notifications}
    assert status_by_house["18 Sher Avenue"] == "MATCH_CAPTURED"


def test_visitor_review_height_database_and_aws_outbox(tmp_path: Path, monkeypatch):
    import json

    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "member-phase3.db"))
    client = TestClient(app)
    image = FACE_FIXTURE.read_bytes()

    calibration = client.put(
        "/api/member/cameras/CAM-U1-01/calibration",
        json={
            "mode": "REFERENCE",
            "image_width": 1254,
            "image_height": 1254,
            "horizon_y": 300,
            "ref_height_m": 2.0,
            "ref_foot_y": 1150,
            "ref_head_y": 350,
            "calibration_score": 100,
            "updated_by": "Test operator",
        },
    )
    assert calibration.status_code == 200

    boxes = [{"x": 300, "y": 350, "width": 300, "height": 800} for _ in range(3)]
    first = client.post(
        "/api/member/face-sightings",
        files={"image": ("face.png", image, "image/png")},
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "browser_confidence": "0.99",
            "person_boxes_json": json.dumps(boxes),
            "image_width": "1254",
            "image_height": "1254",
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["height"]["height_status"] == "ESTIMATED"
    assert first_payload["height"]["height_low_m"] < first_payload["height"]["height_high_m"]
    profile_id = first_payload["profile_id"]

    trusted = client.post(
        f"/api/member/visitors/{profile_id}/classify",
        json={
            "user_id": "USR-001",
            "status": "TRUSTED",
            "display_label": "Gardener",
            "category": "WORKER",
            "notes": "Approved at this household",
            "updated_by": "Test operator",
        },
    )
    assert trusted.status_code == 200
    assert trusted.json()["profile"]["viewer_classification"]["display_label"] == "Gardener"

    recent_user_1 = client.get("/api/member/USR-001/face-sightings")
    assert recent_user_1.status_code == 200
    recent_item = recent_user_1.json()["sightings"][0]
    assert recent_item["effective_label"] == "Gardener"
    assert recent_item["effective_status"] == "TRUSTED"
    assert recent_item["anonymous_label"].startswith("Anonymous visitor")

    # Household trust is local: the same visitor remains unknown to User 2.
    second = client.post(
        "/api/member/face-sightings",
        files={"image": ("face.png", image, "image/png")},
        data={"user_id": "USR-002", "camera_id": "CAM-U2-01", "browser_confidence": "0.99"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["classification"] == "REPEAT_VISITOR_CANDIDATE"
    assert second_payload["viewer_classification"]["status"] == "UNKNOWN"

    recent_user_2 = client.get("/api/member/USR-002/face-sightings")
    assert recent_user_2.status_code == 200
    user_2_item = recent_user_2.json()["sightings"][0]
    assert user_2_item["effective_label"].startswith("Anonymous visitor")
    assert user_2_item["effective_status"] == "UNKNOWN"

    incident = client.post(
        f"/api/member/visitors/{profile_id}/classify",
        json={
            "user_id": "USR-002",
            "status": "CONFIRMED_INTRUDER",
            "notes": "Forced gate entry observed by the member",
            "updated_by": "Test operator",
            "sighting_id": second_payload["sighting_id"],
            "start_incident_watch": True,
            "incident_type": "FORCED_ENTRY",
            "duration_minutes": 15,
        },
    )
    assert incident.status_code == 200
    incident_id = incident.json()["incident"]["incident_id"]

    report = client.get(f"/api/member/incidents/{incident_id}/report")
    assert report.status_code == 200
    assert report.json()["incident"]["status"] == "ACTIVE"

    database = client.get("/api/member/database/overview")
    assert database.status_code == 200
    db_payload = database.json()
    assert db_payload["counts"]["face_sightings"] == 2
    assert db_payload["counts"]["member_profile_labels"] == 2
    assert db_payload["counts"]["member_incidents"] == 1
    assert db_payload["pending_aws_records"] > 0
    assert db_payload["aws_sync_status"] == "LOCAL_PENDING"

    closed = client.post(
        f"/api/member/incidents/{incident_id}/close",
        json={"outcome": "RESOLVED", "notes": "Demo watch closed", "closed_by": "Test operator"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"

    split = client.post(
        f"/api/member/sightings/{second_payload['sighting_id']}/false-match",
        json={"reason": "Human reviewer confirmed a different person", "reviewer": "Test operator"},
    )
    assert split.status_code == 200
    assert split.json()["split"] is True


def test_all_member_review_actions_persist_and_confirm_database_write(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "member-actions.db"))
    client = TestClient(app)
    image = FACE_FIXTURE.read_bytes()

    sighting = client.post(
        "/api/member/face-sightings",
        files={"image": ("face.png", image, "image/png")},
        data={"user_id": "USR-001", "camera_id": "CAM-U1-01", "browser_confidence": "0.99"},
    )
    assert sighting.status_code == 200
    profile_id = sighting.json()["profile_id"]

    cases = [
        ("UNKNOWN", None),
        ("REVIEW_REQUIRED", None),
        ("CLEARED", None),
        ("TRUSTED", "Family friend"),
    ]
    for status, label in cases:
        response = client.post(
            f"/api/member/visitors/{profile_id}/classify",
            json={
                "user_id": "USR-001",
                "status": status,
                "display_label": label,
                "notes": f"UI action test for {status}",
                "updated_by": "Automated UI contract test",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["profile"]["viewer_classification"]["status"] == status
        assert payload["database_write"] == {
            "engine": "SQLite",
            "table": "member_profile_labels",
            "record_id": f"{profile_id}:USR-001",
            "action": "UPSERT",
            "status": status,
            "committed": True,
        }
        assert payload["aws_outbox_pending"] > 0

    overview = client.get("/api/member/database/overview")
    assert overview.status_code == 200
    assert overview.json()["counts"]["member_profile_labels"] == 1
    assert any(
        write["table_name"] == "member_profile_labels"
        for write in overview.json()["recent_writes"]
    )


def test_dashboard_visitor_actions_have_visible_feedback_contract():
    dashboard = REPO_ROOT / "services" / "operations" / "static" / "dashboard.html"
    html = dashboard.read_text(encoding="utf-8")
    for status in ("TRUSTED", "UNKNOWN", "REVIEW_REQUIRED", "CLEARED", "CONFIRMED_INTRUDER"):
        assert f'data-vstatus="{status}"' in html
    assert "Writing to database" in html
    assert "Database write confirmed" in html
    assert "memberActionFeedbackHtml" in html
    assert "setVisitorActionBusy" in html
