from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.claims_bridge import build_metro_patrol, claims_summary
from sentinel_ops.evidence import compare_events
from sentinel_ops.models import (
    AlertEvaluationRequest,
    CameraEvent,
    Claim,
    ReconstructRequest,
)
from sentinel_ops.rewind import reconstruct_incident


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _translate_demo_events(events: list[CameraEvent], target_lat: float, target_lon: float):
    base_lat = events[0].location.latitude
    base_lon = events[0].location.longitude
    lat_shift = target_lat - base_lat
    lon_shift = target_lon - base_lon
    return [
        event.model_copy(
            update={
                "location": event.location.model_copy(
                    update={
                        "latitude": target_lat + (event.location.latitude - base_lat) * 0.35,
                        "longitude": target_lon + (event.location.longitude - base_lon) * 0.35,
                    }
                ),
                "source": "synthetic-camera-demo",
            }
        )
        for event in events
    ]


def run_integrated_demo(metro: str | None = None) -> dict:
    comparison, hotspots, depot, metadata = build_metro_patrol(
        metro or "Gauteng",
        fuel_l_per_100km=10.0,
    )
    selected_metro = hotspots[0].metro or metro or "Pilot"
    top = hotspots[0]

    events = [CameraEvent.model_validate(row) for row in _load("events.json")]
    events = _translate_demo_events(
        events,
        top.location.latitude,
        top.location.longitude,
    )

    claim_fixture = _load("claim.json")
    claim = Claim.model_validate(
        {
            **claim_fixture,
            "location": top.location.model_dump(),
            "incident_time": events[0].timestamp + timedelta(minutes=12),
            "claim_type": top.main_peril or claim_fixture["claim_type"],
        }
    )

    evidence = compare_events(events[0], events[1])
    alert = evaluate_alert(
        AlertEvaluationRequest(
            event=events[1],
            hotspots=hotspots,
            evidence_links=[evidence],
        )
    )
    timeline = reconstruct_incident(
        ReconstructRequest(
            claim=claim,
            events=events,
            radius_km=8,
            minutes_before=90,
            minutes_after=90,
        )
    )

    return {
        "metro": selected_metro,
        "data_source": metadata,
        "claims_summary": claims_summary(hotspots),
        "depot": {
            "hotspot_id": depot.hotspot_id,
            "name": depot.name,
            "location": depot.location.model_dump(),
        },
        "hotspots": [hotspot.model_dump(mode="json") for hotspot in hotspots],
        "evidence": evidence.model_dump(mode="json"),
        "alert": alert.model_dump(mode="json"),
        "timeline": timeline.model_dump(mode="json"),
        "patrol": comparison.model_dump(mode="json"),
        "demo_notice": (
            "Claims hotspots come from the cassoojee pipeline. Camera events and the "
            "claim used for evidence/rewind are synthetic demo fixtures until the "
            "camera workstream supplies live event JSON."
        ),
    }
