from __future__ import annotations

import json
from pathlib import Path

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.enrichment import fuse_operational_context
from sentinel_ops.evidence import compare_events
from sentinel_ops.models import (
    AlertEvaluationRequest,
    CameraEvent,
    Claim,
    EnrichmentRequest,
    EnrichmentRow,
    Hotspot,
    PatrolRequest,
    ReconstructRequest,
)
from sentinel_ops.rewind import reconstruct_incident
from sentinel_ops.routing import optimise_patrol

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_demo() -> dict:
    hotspots = [Hotspot.model_validate(row) for row in _load("hotspots.json")]
    rows = [
        EnrichmentRow.model_validate(row)
        for row in _load("enrichments.json")
    ]
    hotspots = fuse_operational_context(
        EnrichmentRequest(hotspots=hotspots, enrichments=rows)
    )
    events = [CameraEvent.model_validate(row) for row in _load("events.json")]
    claim = Claim.model_validate(_load("claim.json"))

    link = compare_events(events[0], events[1])
    alert = evaluate_alert(
        AlertEvaluationRequest(
            event=events[1],
            hotspots=hotspots,
            evidence_links=[link],
        )
    )
    timeline = reconstruct_incident(
        ReconstructRequest(
            claim=claim,
            events=events,
            radius_km=6,
        )
    )
    patrol = optimise_patrol(
        PatrolRequest(
            start=claim.location,
            hotspots=hotspots,
            baseline_order=["H004", "H002", "H005", "H001", "H003"],
            max_stops=5,
        )
    )
    return {
        "hotspots": [hotspot.model_dump(mode="json") for hotspot in hotspots],
        "evidence": link.model_dump(mode="json"),
        "alert": alert.model_dump(mode="json"),
        "timeline": timeline.model_dump(mode="json"),
        "patrol": patrol.model_dump(mode="json"),
    }
