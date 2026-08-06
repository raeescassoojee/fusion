from datetime import datetime

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.evidence import compare_events
from sentinel_ops.models import (
    AlertEvaluationRequest,
    CameraEvent,
    FaceSignal,
    Hotspot,
    Location,
)
from sentinel_ops.trust_policy import evidence_policy_for_trust


def _event(event_id: str, trust: float, minute: int = 0) -> CameraEvent:
    return CameraEvent(
        event_id=event_id,
        camera_id=f"CAM-{event_id}",
        timestamp=datetime.fromisoformat(f"2026-08-02T18:{minute:02d}:00+02:00"),
        location=Location(latitude=-26.05, longitude=28.03),
        face=FaceSignal(reference_token="CONSENTED-FACE", confidence=0.9),
        camera_trust_score=trust,
        source="test",
    )


def test_p1_trust_policy_boundaries():
    assert evidence_policy_for_trust(85)["band"] == "STRONG"
    assert evidence_policy_for_trust(84.9)["height_enabled"] is False
    assert evidence_policy_for_trust(70)["biometric_escalation_enabled"] is True
    assert evidence_policy_for_trust(69.9)["biometric_escalation_enabled"] is False
    assert evidence_policy_for_trust(50)["alert_enabled"] is True
    assert evidence_policy_for_trust(49.9)["metadata_only"] is True


def test_weak_trust_retains_face_metadata_but_excludes_biometric_score():
    link = compare_events(_event("A", 60), _event("B", 60, 1))
    assert link.components["face_raw_metadata"] > 0
    assert link.components["face"] == 0
    assert link.components["biometric_escalation_enabled"] == 0
    assert any("retained as metadata" in reason for reason in link.reasons)


def test_metadata_only_event_cannot_create_an_alert():
    event = _event("LOW", 49)
    hotspot = Hotspot(
        hotspot_id="H-CRITICAL",
        name="Critical hotspot",
        location=event.location,
        risk_score=100,
    )
    alert = evaluate_alert(
        AlertEvaluationRequest(event=event, hotspots=[hotspot], evidence_links=[])
    )
    assert alert.priority == "NONE"
    assert alert.status == "NO_ALERT"
    assert alert.evidence_policy["metadata_only"] is True
    assert any("Alert escalation blocked" in reason for reason in alert.reasons)
