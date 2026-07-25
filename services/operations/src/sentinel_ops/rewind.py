from __future__ import annotations

from datetime import timedelta

from sentinel_ops.geo import haversine_km
from sentinel_ops.models import IncidentTimeline, ReconstructRequest, TimelineItem


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def reconstruct_incident(request: ReconstructRequest) -> IncidentTimeline:
    start = request.claim.incident_time - timedelta(
        minutes=request.minutes_before
    )
    end = request.claim.incident_time + timedelta(
        minutes=request.minutes_after
    )
    items: list[TimelineItem] = []

    for event in request.events:
        if not start <= event.timestamp <= end:
            continue
        distance = haversine_km(request.claim.location, event.location)
        if distance > request.radius_km:
            continue

        relevance = max(0.0, 35 * (1 - distance / request.radius_km))
        if (
            request.claim.plate_text
            and _norm(request.claim.plate_text) == _norm(event.plate.text)
        ):
            relevance += 35 * event.plate.confidence
        if (
            request.claim.vehicle_colour
            and event.vehicle.colour
            and request.claim.vehicle_colour.lower()
            == event.vehicle.colour.lower()
        ):
            relevance += 12
        if (
            request.claim.vehicle_type
            and event.vehicle.type
            and request.claim.vehicle_type.lower()
            == event.vehicle.type.lower()
        ):
            relevance += 8
        relevance += 10 * event.camera_trust_score / 100
        relevance = min(100.0, relevance)
        if relevance < 20:
            continue

        details = [
            part
            for part in [
                event.plate.text,
                event.vehicle.colour,
                event.vehicle.type,
            ]
            if part
        ]
        items.append(
            TimelineItem(
                event_id=event.event_id,
                timestamp=event.timestamp,
                distance_from_claim_km=round(distance, 3),
                relevance_score=round(relevance, 1),
                description=(
                    f"{' '.join(details) or 'Camera event'}; "
                    f"relevance {relevance:.0f}/100"
                ),
                media_url=event.media_url,
            )
        )

    items.sort(key=lambda item: item.timestamp)
    summary = (
        f"Found {len(items)} relevant events in the incident window."
        if items
        else "No events met the configured filters."
    )
    return IncidentTimeline(
        claim_id=request.claim.claim_id,
        start_time=start,
        end_time=end,
        items=items,
        summary=summary,
    )
