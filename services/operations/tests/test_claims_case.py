from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from sentinel_ops.main import app
from sentinel_ops.member_mesh import initialise_member_store
from sentinel_ops.storage import connect


def test_claim_case_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "claims-case.db"))
    with TestClient(app) as client:
        queue = client.get("/api/fraud/cases/queue?limit=2")
        assert queue.status_code == 200
        source = queue.json()["claims"][0]["incident_id"]

        opened = client.post(f"/api/fraud/cases/open/{source}")
        assert opened.status_code == 200
        data = opened.json()
        case_id = data["case"]["case_id"]
        assert len(data["validations"]) == 10
        assert data["database"]["counts"]["claim_cases"] == 1

        updated = client.post(
            f"/api/fraud/cases/{case_id}/validations",
            json={
                "check_code": "POLICY_ACTIVE",
                "status": "VERIFIED",
                "value": "POL-DEMO",
                "note": "Checked against policy record",
            },
        )
        assert updated.status_code == 200
        policy = next(v for v in updated.json()["validations"] if v["check_code"] == "POLICY_ACTIVE")
        assert policy["status"] == "VERIFIED"

        agent = client.post(f"/api/fraud/cases/{case_id}/agent/run")
        assert agent.status_code == 200
        assert agent.json()["agent"]["status"] == "COMPLETED"
        assert agent.json()["tasks"]

        report = client.post(f"/api/fraud/cases/{case_id}/report/generate")
        assert report.status_code == 200
        assert report.json()["report"]["case_id"] == case_id
        assert report.json()["workspace"]["database"]["counts"]["claim_case_reports"] == 1


def test_latest_member_incident_becomes_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "member-to-claim.db"))
    initialise_member_store()
    now = datetime.now().astimezone().isoformat()
    with connect() as db:
        db.execute(
            """
            INSERT INTO face_profiles(
                profile_id, anonymous_label, embedding, embedding_size, first_seen,
                last_seen, sighting_count, system_status, review_required
            ) VALUES ('PROF-DEMO', 'Anonymous visitor demo', ?, 2, ?, ?, 1, 'UNKNOWN', 0)
            """,
            (b"12345678", now, now),
        )
        db.execute(
            """
            INSERT INTO face_sightings(
                sighting_id, profile_id, user_id, camera_id, captured_at, similarity,
                detection_confidence, latitude, longitude, review_status
            ) VALUES ('SIG-DEMO', 'PROF-DEMO', 'USR-001', 'CAM-U1-01', ?, 1.0, 0.99,
                      -26.198020, 28.310300, 'UNREVIEWED')
            """,
            (now,),
        )
        db.execute(
            """
            INSERT INTO member_incidents(
                incident_id, profile_id, origin_user_id, origin_camera_id,
                origin_sighting_id, incident_type, status, started_at, updated_at,
                duration_minutes, notes
            ) VALUES ('INC-DEMO', 'PROF-DEMO', 'USR-001', 'CAM-U1-01', 'SIG-DEMO',
                      'HOME_INVASION', 'ACTIVE', ?, ?, 30, 'Door forced')
            """,
            (now, now),
        )

    with TestClient(app) as client:
        response = client.post(
            "/api/fraud/cases/from-member/latest",
            json={"claim_amount": 250000, "claim_type": "Home Invasion", "item_type": "Contents"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["case"]["source_type"] == "MEMBER_INCIDENT"
        assert data["case"]["member_incident_id"] == "INC-DEMO"
        assert any(e["evidence_type"] == "MEMBER_INCIDENT" for e in data["evidence"])


def test_camera_inbox_auto_ingest_reconciles_two_real_vehicle_clips(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "camera-inbox.db"))
    from sentinel_ops import claims_case as case_module

    def fake_trace(case_id, upload_id, item, source_path, captured_at):
        plate = item["policy_plate"]
        now = captured_at.isoformat()
        with connect() as db:
            db.execute(
                """
                INSERT INTO claim_plate_scan_frames(
                    frame_read_id, case_id, upload_id, frame_index, video_time_seconds,
                    box_json, raw_ocr, normalized_ocr, ocr_confidence,
                    supported_positions_json, accumulated_display, created_at
                ) VALUES (?, ?, ?, 10, .3, '{}', ?, ?, .7, '[0,1,2]', ?, ?)
                """,
                (f"PFR-{upload_id}", case_id, upload_id, plate[:3], plate[:3], plate[:3] + "·····", now),
            )
            observation_id = f"PLT-{upload_id}"
            payload = {"source_upload_id": upload_id, "policy_plate": plate, "best_raw_ocr": plate[:6], "visual_support": .75}
            db.execute(
                """
                INSERT INTO claim_plate_observations(
                    observation_id, case_id, event_id, plate_text, normalized_plate,
                    ocr_confidence, detection_confidence, camera_id, captured_at,
                    media_url, match_status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, .75, .9, ?, ?, ?, 'POLICY_RECONCILED', ?, ?)
                """,
                (observation_id, case_id, f"TRACE-{upload_id}", plate, plate, item["camera_id"], now, item.get("media_url"), case_module._safe_json(payload), now),
            )
        return {"observation_id": observation_id, "policy_plate": plate, "best_raw_ocr": plate[:6], "visual_support": .75, "evidence_score": .75, "reconciliation_status": "POLICY_RECONCILED", "trace_frames": 1}

    monkeypatch.setattr(case_module, "_build_real_plate_trace", fake_trace)

    with TestClient(app) as client:
        source = client.get("/api/fraud/cases/queue?limit=1").json()["claims"][0]["incident_id"]
        case_id = client.post(f"/api/fraud/cases/open/{source}").json()["case"]["case_id"]
        response = client.post(f"/api/fraud/cases/{case_id}/camera-inbox/auto-ingest")
        assert response.status_code == 200
        data = response.json()
        assert len(data["workspace"]["camera_uploads"]) == 2
        assert all(item["status"] == "PROCESSED" for item in data["workspace"]["camera_uploads"])
        assert data["continuity"]["status"] == "VEHICLE_MISMATCH"
        assert set(data["continuity"]["distinct_vehicles"]) == {"DV70FTGP", "FG47MSGP"}
        assert {item["normalized_plate"] for item in data["workspace"]["plates"]} == {"DV70FTGP", "FG47MSGP"}
        validation = next(item for item in data["workspace"]["validations"] if item["check_code"] == "PLATE_MATCH")
        assert validation["status"] == "MISMATCH"
        assert "DV70FTGP" in validation["value"] and "FG47MSGP" in validation["value"]
        assert data["workspace"]["database"]["counts"]["claim_plate_scan_frames"] == 2

