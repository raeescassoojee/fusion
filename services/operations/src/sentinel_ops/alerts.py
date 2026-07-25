from __future__ import annotations

from uuid import uuid4

from sentinel_ops.geo import haversine_km
from sentinel_ops.models import Alert, AlertEvaluationRequest, Hotspot


def _in_peak(event, hotspot: Hotspot) -> bool:
    days = {day.lower() for day in hotspot.peak_window.days}
    if days and event.timestamp.strftime("%A").lower() not in days:
        return False
    now = event.timestamp.strftime("%H:%M")
    start = hotspot.peak_window.start
    end = hotspot.peak_window.end
    return start <= now <= end if start <= end else now >= start or now <= end


def evaluate_alert(request: AlertEvaluationRequest) -> Alert:
    event = request.event
    candidates = []
    for hotspot in request.hotspots:
        distance = haversine_km(event.location, hotspot.location)
        if distance <= hotspot.geofence_radius_km:
            candidates.append((distance, hotspot))

    evidence_score = max(
        (
            link.score
            for link in request.evidence_links
            if event.event_id in {link.first_event_id, link.second_event_id}
        ),
        default=0.0,
    )

    if not candidates:
        return Alert(
            alert_id=f"ALT-{uuid4().hex[:8].upper()}",
            event_id=event.event_id,
            priority="NONE",
            status="NO_ALERT",
            evidence_score=evidence_score,
            reasons=["Event is outside all configured hotspot geofences"],
        )

    distance, hotspot = min(candidates, key=lambda item: item[0])
    peak = _in_peak(event, hotspot)
    combined = (
        0.45 * hotspot.risk_score
        + 0.35 * evidence_score
        + 10 * int(peak)
        + 0.10 * event.camera_trust_score
    )
    gate = event.camera_trust_score >= 55 and (
        evidence_score >= 45 or hotspot.risk_score >= 80
    )

    if not gate:
        priority = "LOW" if combined >= 45 else "NONE"
    elif combined >= 75:
        priority = "HIGH"
    elif combined >= 60:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    status = "PENDING_REVIEW" if priority != "NONE" else "NO_ALERT"
    reasons = [
        f"Event is {distance:.2f} km from hotspot {hotspot.hotspot_id}",
        "Inside peak-risk window" if peak else "Outside peak-risk window",
        f"Evidence score is {evidence_score:.1f}/100",
        f"Camera trust is {event.camera_trust_score:.1f}/100",
    ]
    return Alert(
        alert_id=f"ALT-{uuid4().hex[:8].upper()}",
        event_id=event.event_id,
        priority=priority,
        status=status,
        hotspot_id=hotspot.hotspot_id,
        evidence_score=round(evidence_score, 1),
        reasons=reasons,
    )
