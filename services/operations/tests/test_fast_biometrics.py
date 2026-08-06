from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sentinel_ops.main import app
from sentinel_ops.member_mesh import (
    _face_evidence_quality,
    _far_retrieval_evidence_eligible,
    _far_retrieval_requirement,
    _gallery_candidates,
    _new_profile_evidence_strong,
    _track_continuity_candidate,
    initialise_member_store,
    invalidate_face_gallery,
)
from sentinel_ops.storage import connect

REPO_ROOT = Path(__file__).resolve().parents[3]
FACE_FIXTURE = REPO_ROOT / "media" / "synthetic_face_fixture.png"


def test_vectorised_gallery_returns_runner_up_margin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "gallery.db"))
    initialise_member_store()
    first = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    second = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    with connect() as db:
        for profile_id, vector in (("FACE-A", first), ("FACE-B", second)):
            db.execute(
                """
                INSERT INTO face_profiles(
                    profile_id, anonymous_label, embedding, embedding_size,
                    first_seen, last_seen, sighting_count
                ) VALUES (?, ?, ?, ?, '2026-08-01T00:00:00+02:00',
                          '2026-08-01T00:00:00+02:00', 1)
                """,
                (profile_id, profile_id, vector.tobytes(), vector.size),
            )
        best, runner_up = _gallery_candidates(db, np.array([0.99, 0.01, 0.0, 0.0], dtype=np.float32))
    assert best is not None and best["row"]["profile_id"] == "FACE-A"
    assert runner_up is not None and runner_up["row"]["profile_id"] == "FACE-B"
    assert best["similarity"] - runner_up["similarity"] > 0.4


def test_large_face_is_not_stuck_behind_soft_quality_gate():
    crop = np.zeros((112, 112, 3), dtype=np.uint8)
    quality = _face_evidence_quality(
        crop,
        {"x": 0, "y": 0, "width": 112, "height": 112},
        detector_confidence=0.0,
    )
    assert quality["overall"] < 42
    assert quality["large_face_override"] is True
    assert quality["eligible"] is True
    assert quality["state"] == "RECOGNITION_ELIGIBLE"


def test_new_profile_requires_stronger_face_than_candidate_retrieval():
    assert _new_profile_evidence_strong(
        {"face_pixels": 128, "overall": 40}, original_face_pixels=128
    ) is True
    assert _new_profile_evidence_strong(
        {"face_pixels": 128, "overall": 80}, original_face_pixels=72
    ) is False


def test_far_retrieval_uses_original_pixels_and_never_upscale_claims():
    quality = {"face_pixels": 128, "overall": 70}
    assert _far_retrieval_evidence_eligible(quality, 48) is True
    assert _far_retrieval_evidence_eligible(quality, 42) is True
    assert _far_retrieval_evidence_eligible(quality, 41) is False
    assert _far_retrieval_requirement(47) == (4, 0.72)
    assert _far_retrieval_requirement(48) == (3, 0.68)
    assert _far_retrieval_evidence_eligible(
        {"face_pixels": 256, "overall": 34.9}, 80
    ) is False


def test_far_retrieval_never_enrols_a_new_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "far-retrieval.db"))
    client = TestClient(app)
    image = FACE_FIXTURE.read_bytes()
    response = client.post(
        "/api/member/face-sightings/batch",
        files=[
            ("images", (f"far-{index}.png", image, "image/png"))
            for index in range(4)
        ],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": json.dumps([
                {
                    "track_id": "TRACK-FAR",
                    "sample_index": index,
                    "confidence": 0.90,
                    "quality": 70,
                    "face_pixels": 48,
                    "retrieval_only": True,
                }
                for index in range(4)
            ]),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["rejected"][0]["detail"]["code"] == "FACE_FAR_RETRIEVAL_UNCERTAIN"
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM face_profiles").fetchone()["n"] == 0
    assert _new_profile_evidence_strong(
        {"face_pixels": 95, "overall": 100}, original_face_pixels=95
    ) is False


def test_live_observed_trust_can_only_downgrade_server_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "observed-trust.db"))
    client = TestClient(app)
    response = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("face.png", FACE_FIXTURE.read_bytes(), "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": json.dumps([{
                "track_id": "TRACK-LOW-TRUST",
                "confidence": 0.99,
                "quality": 92,
                "face_pixels": 128,
                "observed_camera_trust": 49,
            }]),
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["camera"]["camera_trust"] == 49
    assert result["evidence_policy"]["metadata_only"] is True
    assert result["evidence_policy"]["alert_enabled"] is False


def test_live_track_continuity_is_face_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "continuity.db"))
    initialise_member_store()
    vector = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    with connect() as db:
        db.execute(
            """
            INSERT INTO face_profiles(
                profile_id, anonymous_label, embedding, embedding_size,
                first_seen, last_seen, sighting_count
            ) VALUES ('FACE-STICKY', 'Sticky face', ?, 4,
                      '2026-08-01T00:00:00+02:00',
                      '2026-08-01T00:00:00+02:00', 1)
            """,
            (vector.tobytes(),),
        )
        candidate = _track_continuity_candidate(
            db, "FACE-STICKY", np.array([0.99, 0.01, 0.0, 0.0], dtype=np.float32)
        )
    assert candidate is not None
    assert candidate["row"]["profile_id"] == "FACE-STICKY"
    assert candidate["similarity"] > 0.99


def test_tracked_batch_cross_camera_and_profile_poisoning_guard(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "fast-batch.db"))
    image = FACE_FIXTURE.read_bytes()
    client = TestClient(app)

    first = client.post(
        "/api/member/face-sightings/batch",
        files=[
            ("images", (f"track-1-{index}.png", image, "image/png"))
            for index in range(3)
        ],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": json.dumps([
                {
                    "track_id": "TRACK-1",
                    "sample_index": index,
                    "confidence": 0.99,
                    "quality": 92 - index,
                }
                for index in range(3)
            ]),
        },
    )
    assert first.status_code == 200
    first_result = first.json()["results"][0]
    assert first_result["classification"] == "NEW_VISITOR"
    assert first_result["samples_considered"] == 3
    assert first_result["signature_method"] == "QUALITY_WEIGHTED_MULTI_FRAME"
    profile_id = first_result["profile_id"]
    assert first_result["sighting"]["sighting_id"] == first_result["sighting_id"]
    recent = client.get("/api/member/USR-001/face-sightings")
    assert recent.status_code == 200
    assert recent.headers["cache-control"] == "no-store, max-age=0"
    assert recent.json()["sightings"][0]["profile_id"] == profile_id
    visitors = client.get("/api/member/visitors", params={"user_id": "USR-001"})
    assert visitors.status_code == 200
    assert visitors.json()["visitors"][0]["profile_id"] == profile_id
    with connect() as db:
        before = bytes(db.execute(
            "SELECT embedding FROM face_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()["embedding"])

    second = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("track-2.png", image, "image/png"))],
        data={
            "user_id": "USR-002",
            "camera_id": "CAM-U2-01",
            "candidates_json": json.dumps([
                {"track_id": "TRACK-2", "confidence": 0.99, "quality": 91}
            ]),
        },
    )
    assert second.status_code == 200
    result = second.json()["results"][0]
    assert result["classification"] == "REPEAT_VISITOR_CANDIDATE"
    assert result["track_id"] == "TRACK-2"
    assert result["continuity"]["profile_updated_from_candidate"] is False
    assert result["match_margin"] >= result["margin_threshold"]
    with connect() as db:
        after = bytes(db.execute(
            "SELECT embedding FROM face_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()["embedding"])
    assert after == before


def test_confirmed_intruder_stays_red_across_household_sightings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "intruder-visible.db"))
    image = FACE_FIXTURE.read_bytes()
    client = TestClient(app)
    first = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("track-1.png", image, "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": '[{"track_id":"TRACK-1","confidence":0.99,"quality":92}]',
        },
    ).json()["results"][0]
    classified = client.post(
        f"/api/member/visitors/{first['profile_id']}/classify",
        json={
            "user_id": "USR-001",
            "status": "CONFIRMED_INTRUDER",
            "notes": "Human-reviewed test incident",
            "updated_by": "Test operator",
            "start_incident_watch": False,
        },
    )
    assert classified.status_code == 200

    second = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("track-2.png", image, "image/png"))],
        data={
            "user_id": "USR-002",
            "camera_id": "CAM-U2-01",
            "candidates_json": '[{"track_id":"TRACK-2","confidence":0.99,"quality":92}]',
        },
    )
    assert second.status_code == 200
    result = second.json()["results"][0]
    assert result["profile_status"] == "CONFIRMED_INTRUDER"
    assert result["viewer_classification"]["status"] == "UNKNOWN"

    recent = client.get("/api/member/USR-002/face-sightings").json()["sightings"]
    current = next(item for item in recent if item["sighting_id"] == result["sighting_id"])
    assert current["effective_status"] == "CONFIRMED_INTRUDER"
    assert current["effective_label"] == "Anonymous visitor 01"


def test_duplicate_reviewed_profiles_do_not_create_another_profile(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "duplicate-reviewed.db"))
    image = FACE_FIXTURE.read_bytes()
    client = TestClient(app)
    first = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("first.png", image, "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": '[{"track_id":"TRACK-1","confidence":0.99,"quality":92}]',
        },
    ).json()["results"][0]
    labelled = client.post(
        f"/api/member/visitors/{first['profile_id']}/classify",
        json={
            "user_id": "USR-001",
            "status": "CONFIRMED_INTRUDER",
            "display_label": "Mo Intruder",
            "notes": "Human-reviewed duplicate test",
            "updated_by": "Test operator",
            "start_incident_watch": False,
        },
    )
    assert labelled.status_code == 200

    with connect() as db:
        source = db.execute(
            "SELECT embedding, embedding_size FROM face_profiles WHERE profile_id=?",
            (first["profile_id"],),
        ).fetchone()
        db.execute(
            """
            INSERT INTO face_profiles(
                profile_id, anonymous_label, embedding, embedding_size, first_seen,
                last_seen, sighting_count, system_status, review_required
            ) VALUES ('FACE-DUPLICATE', 'Anonymous visitor duplicate', ?, ?,
                      '2026-08-02T10:00:00+02:00', '2026-08-02T10:00:00+02:00',
                      1, 'CONFIRMED_INTRUDER', 0)
            """,
            (source["embedding"], source["embedding_size"]),
        )
        db.execute(
            """
            INSERT INTO member_profile_labels(
                profile_id, user_id, status, display_label, notes, updated_at, updated_by
            ) VALUES ('FACE-DUPLICATE', 'USR-001', 'CONFIRMED_INTRUDER',
                      '  mo   intruder ', 'Reviewed duplicate',
                      '2026-08-02T10:00:00+02:00', 'Test operator')
            """
        )
    invalidate_face_gallery()

    repeated = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("repeat.png", image, "image/png"))],
        data={
            "user_id": "USR-002",
            "camera_id": "CAM-U2-01",
            "candidates_json": '[{"track_id":"TRACK-2","confidence":0.99,"quality":92}]',
        },
    )
    assert repeated.status_code == 200
    result = repeated.json()["results"][0]
    assert result["matched"] is True
    assert result["duplicate_reviewed_identity"] is True
    assert result["continuity"]["runner_up_same_reviewed_identity"] is True
    assert result["reviewed_display_label"].strip().lower() == "mo intruder"
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM face_profiles").fetchone()["n"] == 2


def test_dashboard_fast_batch_renders_recent_and_intruder_colours():
    dashboard = REPO_ROOT / "services" / "operations" / "static" / "dashboard.html"
    html = dashboard.read_text(encoding="utf-8")
    assert "memberUpsertRecentSighting(d)" in html
    assert "intruder-sighting" in html
    assert "MATCHED & SAVED" in html
    assert "largeFaceOverride" in html
    assert "setTimeout(()=>memberSendFastBatch(false),0)" in html
    assert "memberScheduleFastFollowup(track)" in html
    assert "delay=190" in html
    assert "memberTrackHasEvidence(t,manual)" in html
    assert "return track.samples.length>=1" in html
    assert "now-t.lastSeen<5000" in html
    assert "t.hits>=2" not in html
    assert "memberUpdateFastTracks(memberLastFaces,Date.now());memberSchedulePersonDetection" not in html
    assert "fd.append('person_boxes_json','[]')" in html
    assert "continuity_profile_id:track.profileId||null" in html
    assert "face_pixels:sample.facePixels" in html
    assert "fd.append('contexts'" not in html
    assert "memberFullBodyVisible(person)" in html
    assert "memberLastPersons.filter(p=>memberFullBodyVisible(p,W,H))" in html
    assert "memberLastPersons.slice(0,1).forEach" not in html
    assert "if(!memberLatestSighting||memberBatchBusy||!objModel" in html
    assert "await faceModel.estimateFaces(warm,false)" in html
    assert html.index("faceModel=await blazeface.load()") < html.index("objModel=await cocoSsd.load(")
    assert "/api/member/sightings/${encodeURIComponent(sightingId)}/height" in html
    assert "data-sighting-id" in html
    assert "await objModel.detect(memberVideo);persons=" not in html
    assert "find(d=>d.track_id===memberPanelTrackId)" in html
    assert "memberPanelTrackId=d.track_id" not in html


def test_dashboard_v7_has_per_person_cards_far_rescue_and_on_demand_height():
    dashboard = REPO_ROOT / "services" / "operations" / "static" / "dashboard.html"
    html = dashboard.read_text(encoding="utf-8")
    assert "MEMBER_IDENTITY_HOLD_MS=45000" in html
    assert "memberLiveCards=new Map()" in html
    assert "data-live-person" in html
    assert "data-measure-height" in html
    assert "memberRequestHeight(measure.dataset.measureHeight)" in html
    assert "memberBodyForTrack(persons,track)" in html
    assert "memberSyncTrackHeight(track)" in html
    assert "memberFacesFromPersonCrop(person,index)" in html
    assert "retrieval_only:Boolean(track.retrievalOnly)" in html
    assert "FAR_RETRIEVAL_ELIGIBLE" in html
    assert "requestVideoFrameCallback" in html
    assert "memberOverlay.width!==W||memberOverlay.height!==H" in html
    assert "width:{ideal:1920}" in html
    assert "FULL HEIGHT" in html
    assert "APPROX HEIGHT" in html


def test_dashboard_v8_has_partial_height_far_safety_and_trust_cards():
    dashboard = REPO_ROOT / "services" / "operations" / "static" / "dashboard.html"
    html = dashboard.read_text(encoding="utf-8")
    assert "memberHeightObservation(person,track" in html
    assert "FACE_BODY_TRACK_COMPLETION" in html
    assert "APPROX HEIGHT" in html
    assert "memberFarRequiredFrames" in html
    assert "MEMBER_FAR_MIN_PIXELS=42" in html
    assert "MEMBER_FAST_MIN_PIXELS=56" in html
    assert "MEMBER_FAST_DETECT_MAX_WIDTH=960" in html
    assert "faceModel.estimateFaces(memberDetectCanvas,false)" in html
    assert "now-memberFastStartedAt<500" in html
    assert "observed_camera_trust:sample.observedTrust" in html
    assert "memberCurrentCameraTrust=trust" in html
    assert "memberCarryProfileForBox=function(box){\n  return null;" in html
    assert "evidencePolicyForTrust" in html
    assert "memberTrustPolicyHtml" in html
    assert "multi-frame OCR" in html


def test_fast_batch_and_delayed_height_use_real_full_body_frames(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "fast-height.db"))
    image = FACE_FIXTURE.read_bytes()
    client = TestClient(app)
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
            "updated_by": "Fast height test",
        },
    )
    assert calibration.status_code == 200
    boxes = [{"x": 300, "y": 350, "width": 300, "height": 800} for _ in range(3)]
    batch = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("track-height.png", image, "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": '[{"track_id":"TRACK-HEIGHT","confidence":0.99,"quality":92}]',
            "person_boxes_json": json.dumps(boxes),
            "image_width": "1254",
            "image_height": "1254",
        },
    )
    assert batch.status_code == 200
    sighting = batch.json()["results"][0]
    assert sighting["height"]["height_status"] == "ESTIMATED"
    assert sighting["height"]["frames_used"] == 3

    delayed = client.post(
        f"/api/member/sightings/{sighting['sighting_id']}/height",
        json={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "person_boxes": boxes,
            "image_width": 1254,
            "image_height": 1254,
        },
    )
    assert delayed.status_code == 200
    payload = delayed.json()
    assert payload["updated"] is True
    assert payload["height"]["height_status"] == "ESTIMATED"
    assert payload["sighting"]["height_low_m"] is not None

    recent = client.get("/api/member/USR-001/face-sightings").json()["sightings"]
    updated = next(item for item in recent if item["sighting_id"] == sighting["sighting_id"])
    assert updated["height_status"] == "ESTIMATED"
    assert updated["height_low_m"] is not None


def test_partial_body_height_returns_labelled_ten_centimetre_band(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "partial-height.db"))
    client = TestClient(app)
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
            "updated_by": "Partial height test",
        },
    )
    assert calibration.status_code == 200
    first = client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("partial-height.png", FACE_FIXTURE.read_bytes(), "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": '[{"track_id":"TRACK-PARTIAL","confidence":0.99,"quality":92}]',
        },
    ).json()["results"][0]
    partial_boxes = [
        {
            "x": 300,
            "y": 350,
            "width": 300,
            "height": 904,
            "head_y": 350,
            "foot_y": 1150,
            "partial": True,
            "confidence": 0.8,
        }
        for _ in range(5)
    ]
    response = client.post(
        f"/api/member/sightings/{first['sighting_id']}/height",
        json={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "person_boxes": partial_boxes,
            "image_width": 1254,
            "image_height": 1254,
        },
    )
    assert response.status_code == 200
    height = response.json()["height"]
    assert height["height_status"] == "APPROXIMATE_ESTIMATE"
    assert height["approximate"] is True
    assert height["identity_input"] is False
    assert height["frames_used"] == 5
    assert height["height_high_m"] - height["height_low_m"] >= 0.19


def test_full_reset_is_idempotent_and_resets_metrics(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "full-reset.db"))
    client = TestClient(app)
    image = FACE_FIXTURE.read_bytes()
    client.post(
        "/api/member/face-sightings/batch",
        files=[("images", ("track.png", image, "image/png"))],
        data={
            "user_id": "USR-001",
            "camera_id": "CAM-U1-01",
            "candidates_json": '[{"track_id":"TRACK-1","confidence":0.99,"quality":90}]',
        },
    )
    first = client.delete("/api/demo/reset?full=true")
    second = client.delete("/api/demo/reset?full=true")
    assert first.status_code == second.status_code == 200
    assert first.json()["reset"] is True
    assert second.json()["removed"]["member_mesh"]["removed"]["face_sightings"] == 0
    assert second.json()["performance"]["metrics"] == {}
