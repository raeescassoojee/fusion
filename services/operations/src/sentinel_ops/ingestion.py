from __future__ import annotations

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.claims_bridge import load_claims_hotspots
from sentinel_ops.evidence import compare_events
from sentinel_ops.models import (
    AlertEvaluationRequest,
    CameraEvent,
    Claim,
    ReconstructRequest,
)
from sentinel_ops.rewind import reconstruct_incident
from sentinel_ops.storage import (
    list_events,
    save_alert,
    save_claim,
    save_event,
)


def ingest_event(event: CameraEvent) -> dict:
    previous = [item for item in list_events(limit=200) if item.event_id != event.event_id]
    save_event(event)

    candidates = []
    for prior in previous:
        link = compare_events(prior, event)
        if link.score >= 45:
            candidates.append(link)
    candidates.sort(key=lambda item: item.score, reverse=True)

    hotspots, metadata = load_claims_hotspots()
    alert = evaluate_alert(
        AlertEvaluationRequest(
            event=event,
            hotspots=hotspots,
            evidence_links=candidates,
        )
    )
    if alert.status == "PENDING_REVIEW":
        save_alert(alert)

    return {
        "event": event.model_dump(mode="json"),
        "candidate_links": [item.model_dump(mode="json") for item in candidates[:10]],
        "alert": alert.model_dump(mode="json"),
        "hotspot_source": metadata,
    }


def ingest_claim(claim: Claim) -> dict:
    save_claim(claim)
    timeline = reconstruct_incident(
        ReconstructRequest(
            claim=claim,
            events=list_events(limit=500),
            radius_km=8,
            minutes_before=120,
            minutes_after=120,
        )
    )
    return {
        "claim": claim.model_dump(mode="json"),
        "timeline": timeline.model_dump(mode="json"),
    }
