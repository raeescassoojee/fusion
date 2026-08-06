from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from sentinel_ops.main import app
from sentinel_ops.member_mesh import initialise_member_store
from sentinel_ops.models import CameraEvent
from sentinel_ops.storage import connect, save_event


def test_ai_populated_claim_preview_and_checklist_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "claims-progress.db"))
    with TestClient(app) as client:
        queue = client.get("/api/fraud/cases/queue", params={"q": "INC-002", "limit": 10})
        assert queue.status_code == 200
        row = next(item for item in queue.json()["claims"] if item["incident_id"] == "INC-002")
        assert row["police_case_number"].startswith("CAS 122/06/")
        assert row["police_case_number"].endswith("Boksburg SAPS")
        assert row["ai_status"] == "AI_POPULATED"
        assert row["workflow_stage"] == "AI_POPULATED_INFORMATION"
        assert row["checklist_completion_percent"] == 30
        assert row["checks_complete"] == 3

        opened = client.post("/api/fraud/cases/open/INC-002")
        assert opened.status_code == 200
        workspace = opened.json()
        assert workspace["checklist_progress"] == {
            "complete": 3,
            "total": 9,
            "attention": 0,
            "pending": 6,
            "percent": 30,
            "stage": "AI_POPULATED_INFORMATION",
            "label": "AI populated information",
        }
        validations = {item["check_code"]: item for item in workspace["validations"]}
        assert validations["POLICE_REFERENCE"]["label"] == "Police case number and SAPS station"
        assert validations["POLICE_REFERENCE"]["status"] == "PENDING"
        assert validations["POLICE_REFERENCE"]["value"].endswith("Boksburg SAPS")
        assert "Claim form" in validations["SUPPORTING_DOCUMENTS"]["value"]
        assert validations["POLICY_ACTIVE"]["value"].startswith("POL-GP-")

        reviewed = client.post(f"/api/fraud/cases/{workspace['case']['case_id']}/agent/run")
        assert reviewed.status_code == 200
        reviewed_workspace = reviewed.json()
        assert reviewed_workspace["checklist_progress"]["stage"] == "READY_FOR_REVIEW"
        assert reviewed_workspace["checklist_progress"]["label"] == "Ready for review"
        assert reviewed_workspace["checklist_progress"]["percent"] >= 30
        reviewed_validations = {
            item["check_code"]: item for item in reviewed_workspace["validations"]
        }
        assert reviewed_validations["POLICE_REFERENCE"]["status"] == "VERIFIED"


def test_claim_case_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "claims-case.db"))
    with TestClient(app) as client:
        queue = client.get("/api/fraud/cases/queue?limit=2")
        assert queue.status_code == 200
        queue_data = queue.json()
        assert queue_data["data_provenance"]["kind"] == "SUPPLIED_WORKBOOK"
        assert queue_data["data_provenance"]["randomly_generated"] is False
        assert queue_data["data_provenance"]["row_count"] >= len(queue_data["claims"])
        assert queue_data["camera_inbox"]["real_clip_count"] == 2
        source = queue_data["claims"][0]["incident_id"]

        opened = client.post(f"/api/fraud/cases/open/{source}")
        assert opened.status_code == 200
        data = opened.json()
        case_id = data["case"]["case_id"]
        assert len(data["validations"]) == 10
        assert data["database"]["counts"]["claim_cases"] == 1
        assert data["case"]["claim"]["demo_intake_data"] is True
        assert data["case"]["claim"]["policy_status"]
        assert data["case"]["claim"]["claimant_identity_status"]
        assert data["case"]["claim"]["documents_status"]

        timeline_response = client.get(
            f"/api/fraud/cases/{case_id}/timeline",
            params={"minutes_before": 120, "minutes_after": 90, "radius_km": 12},
        )
        assert timeline_response.status_code == 200
        timeline = timeline_response.json()
        assert timeline["claim"]["case_id"] == case_id
        assert timeline["search"]["minutes_before"] == 120
        assert timeline["search"]["minutes_after"] == 90
        assert timeline["search"]["radius_km"] == 12
        assert timeline["story"]["human_review_required"] is True
        assert any(
            step["step_type"] == "CLAIM_INCIDENT"
            for step in timeline["story"]["steps"]
        )
        assert timeline["story"]["steps"] == sorted(
            timeline["story"]["steps"], key=lambda step: step["timestamp"]
        )
        assert isinstance(timeline["nearby_cameras"], list)
        assert isinstance(timeline["linked_evidence"], list)

        # The supplied workbook stores naive incident times while newer camera
        # rows include +02:00; legacy camera rows can have the opposite shape.
        # Both combinations must be normalised instead of raising a 500.
        incident_at = datetime.fromisoformat(timeline["claim"]["incident_time"])
        aware_incident = (
            incident_at
            if incident_at.tzinfo is not None
            else incident_at.replace(tzinfo=timezone(timedelta(hours=2)))
        )
        naive_incident = incident_at.replace(tzinfo=None)
        for event_id, captured_at in (
            ("EVT-AWARE-TIME", aware_incident),
            ("EVT-LEGACY-NAIVE-TIME", naive_incident),
        ):
            save_event(CameraEvent.model_validate({
                "event_id": event_id,
                "camera_id": f"CAM-{event_id}",
                "timestamp": captured_at,
                "location": timeline["claim"]["location"],
                "plate": {"text": "LEGACYGP", "confidence": 0.8},
                "camera_trust_score": 80,
                "source": "mixed-timezone-test",
            }))
        mixed_timestamp_response = client.get(
            f"/api/fraud/cases/{case_id}/timeline",
            params={"minutes_before": 120, "minutes_after": 90, "radius_km": 12},
        )
        assert mixed_timestamp_response.status_code == 200
        mixed_timeline = mixed_timestamp_response.json()
        assert {"EVT-AWARE-TIME", "EVT-LEGACY-NAIVE-TIME"}.issubset(
            {item["event_id"] for item in mixed_timeline["items"]}
        )

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
        automated = {
            item["check_code"]: item for item in agent.json()["validations"]
        }
        assert automated["CLAIMANT_IDENTITY"]["status"] != "PENDING"
        assert automated["POLICY_ACTIVE"]["status"] != "PENDING"
        assert automated["OWNERSHIP"]["status"] != "PENDING"
        assert automated["SUPPORTING_DOCUMENTS"]["status"] != "PENDING"
        assert automated["INCIDENT_TIME"]["status"] == "VERIFIED"
        assert automated["INCIDENT_DESCRIPTION"]["status"] == "VERIFIED"

        report = client.post(f"/api/fraud/cases/{case_id}/report/generate")
        assert report.status_code == 200
        assert report.json()["report"]["case_id"] == case_id
        assert report.json()["workspace"]["database"]["counts"]["claim_case_reports"] == 1

        refreshed = client.get("/api/fraud/cases/queue", params={"q": source})
        assert refreshed.status_code == 200
        row = next(item for item in refreshed.json()["claims"] if item["incident_id"] == source)
        assert row["ai_status"] == "COMPLETED"
        assert row["ai_readiness_score"] is not None
        assert row["checks_complete"] > 0
        assert row["checks_total"] == 9
        assert row["report_ready"] is True
        assert row["report_version"] == 1


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


def test_camera_inbox_auto_ingest_uses_only_cassoojee_claim_clip(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "camera-inbox.db"))
    from sentinel_ops import claims_case as case_module
    trace_calls = []

    def fake_trace(case_id, upload_id, item, source_path, captured_at):
        trace_calls.append(upload_id)
        plate = item["policy_plate"]
        now = captured_at.isoformat()
        with connect() as db:
            db.execute(
                "DELETE FROM claim_plate_scan_frames WHERE case_id=? AND upload_id=?",
                (case_id, upload_id),
            )
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
        source = "INC-002"
        case_id = client.post(f"/api/fraud/cases/open/{source}").json()["case"]["case_id"]
        response = client.post(f"/api/fraud/cases/{case_id}/camera-inbox/auto-ingest")
        assert response.status_code == 200
        data = response.json()
        assert len(data["workspace"]["camera_uploads"]) == 1
        assert all(item["status"] == "PROCESSED" for item in data["workspace"]["camera_uploads"])
        assert data["continuity"]["status"] == "SINGLE_VEHICLE"
        assert data["continuity"]["distinct_vehicles"] == ["DV70FTGP"]
        assert {item["normalized_plate"] for item in data["workspace"]["plates"]} == {"DV70FTGP"}
        validation = next(item for item in data["workspace"]["validations"] if item["check_code"] == "PLATE_MATCH")
        assert validation["status"] != "MISMATCH"
        assert data["workspace"]["database"]["counts"]["claim_plate_scan_frames"] == 1
        assert len(trace_calls) == 1

        # Simulate the all-dot cache written by the previous build, which had no
        # reliable OCR-engine status. It must be reprocessed automatically.
        legacy = data["workspace"]["camera_uploads"][0]
        legacy_payload = dict(legacy["payload"])
        legacy_payload.pop("ocr_engine", None)
        legacy_payload.pop("ocr_engine_available", None)
        legacy_payload.pop("ocr_error", None)
        with connect() as db:
            db.execute(
                "UPDATE claim_plate_scan_frames SET raw_ocr=NULL, normalized_ocr=NULL "
                "WHERE case_id=? AND upload_id=?",
                (case_id, legacy["upload_id"]),
            )
            db.execute(
                "UPDATE claim_camera_uploads SET payload_json=? WHERE case_id=? AND upload_id=?",
                (case_module._safe_json(legacy_payload), case_id, legacy["upload_id"]),
            )
        rerun = client.post(f"/api/fraud/cases/{case_id}/camera-inbox/auto-ingest")
        assert rerun.status_code == 200
        assert len(trace_calls) == 2


def test_relaxed_ocr_keeps_real_demo_clips_readable():
    import cv2
    import difflib
    import json
    from pathlib import Path

    from sentinel_ops.claims_case import _relaxed_plate_ocr

    root = Path(__file__).resolve().parents[3]
    fixture = json.loads(
        (root / "services" / "operations" / "fixtures" / "claims_camera_inbox.json")
        .read_text(encoding="utf-8")
    )
    reads = {}
    for item in fixture["uploads"]:
        point = item["plate_track"][len(item["plate_track"]) // 2]
        capture = cv2.VideoCapture(str(root / item["relative_media_path"]))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.set(cv2.CAP_PROP_POS_FRAMES, round(float(point["time"]) * fps))
        ok, frame = capture.read()
        capture.release()
        assert ok
        x = round(float(point["x"]) * width)
        y = round(float(point["y"]) * height)
        w = round(float(point["width"]) * width)
        h = round(float(point["height"]) * height)
        pad_x, pad_y = round(w * 0.08), round(h * 0.18)
        crop = frame[
            max(0, y - pad_y):min(height, y + h + pad_y),
            max(0, x - pad_x):min(width, x + w + pad_x),
        ]
        reads[item["policy_plate"]] = _relaxed_plate_ocr(
            crop, item["policy_plate"]
        )[0]

    dented = reads["DV70FTGP"] or ""
    driveway = reads["FG47MSGP"] or ""
    assert difflib.SequenceMatcher(a="DV70FTGP", b=dented).ratio() >= 0.75
    assert len(driveway) >= 2
