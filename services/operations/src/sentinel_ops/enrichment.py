from __future__ import annotations

from sentinel_ops.models import EnrichmentRequest, Hotspot


def fuse_operational_context(request: EnrichmentRequest) -> list[Hotspot]:
    total = sum(request.weights.values())
    if total <= 0:
        raise ValueError("Weights must sum to a positive value")
    w = {k: v / total for k, v in request.weights.items()}
    rows = {row.hotspot_id: row for row in request.enrichments}
    output = []
    for hotspot in request.hotspots:
        row = rows.get(hotspot.hotspot_id)
        if not row:
            output.append(hotspot)
            continue
        claims = hotspot.claims_risk_score or hotspot.risk_score
        priority = (
            w.get("claims", 0) * claims
            + w.get("saps", 0) * row.saps_context_score
            + w.get("camera_gap", 0) * (100 - row.camera_coverage_score)
            + w.get("response_gap", 0) * (100 - row.response_success_score)
        )
        output.append(hotspot.model_copy(update={
            "claims_risk_score": round(claims, 1),
            "saps_context_score": round(row.saps_context_score, 1),
            "camera_coverage_score": round(row.camera_coverage_score, 1),
            "response_success_score": round(row.response_success_score, 1),
            "operational_priority": round(priority, 1),
        }))
    return output
