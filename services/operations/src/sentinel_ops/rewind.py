from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sentinel_ops.geo import haversine_km
from sentinel_ops.models import IncidentTimeline, ReconstructRequest, TimelineItem


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def comparable_timestamp(
    value: datetime,
    assumed_timezone=None,
) -> datetime:
    """Return one UTC timestamp even when legacy rows omitted an offset.

    Older local camera rows can contain naive ISO timestamps while newer claim
    rows carry an explicit Africa/Johannesburg offset. Python deliberately
    refuses to compare those two forms. Treat an omitted event offset as the
    incident's timezone (or UTC only when neither side supplied one).
    """
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=assumed_timezone or timezone.utc)
    return value.astimezone(timezone.utc)


def reconstruct_incident(request: ReconstructRequest) -> IncidentTimeline:
    start = request.claim.incident_time - timedelta(
        minutes=request.minutes_before
    )
    end = request.claim.incident_time + timedelta(
        minutes=request.minutes_after
    )
    assumed_timezone = request.claim.incident_time.tzinfo or next(
        (
            event.timestamp.tzinfo
            for event in request.events
            if event.timestamp.tzinfo is not None
            and event.timestamp.utcoffset() is not None
        ),
        timezone.utc,
    )
    start_comparable = comparable_timestamp(start, assumed_timezone)
    end_comparable = comparable_timestamp(end, assumed_timezone)
    items: list[TimelineItem] = []

    for event in request.events:
        event_comparable = comparable_timestamp(event.timestamp, assumed_timezone)
        if not start_comparable <= event_comparable <= end_comparable:
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
        signals: list[str] = []
        if event.plate.text:
            signals.append("NUMBER_PLATE")
        if event.vehicle.colour or event.vehicle.type:
            signals.append("VEHICLE_APPEARANCE")
        if event.face.reference_token or event.face.embedding:
            signals.append("FACE_CANDIDATE")
        if (
            event.appearance.upper_colour
            or event.appearance.lower_colour
            or event.appearance.descriptor_token
        ):
            signals.append("PERSON_APPEARANCE")
        items.append(
            TimelineItem(
                event_id=event.event_id,
                camera_id=event.camera_id,
                timestamp=event.timestamp,
                distance_from_claim_km=round(distance, 3),
                relevance_score=round(relevance, 1),
                description=(
                    f"{' '.join(details) or 'Camera event'}; "
                    f"relevance {relevance:.0f}/100"
                ),
                media_url=event.media_url,
                evidence_signals=signals,
                camera_trust_score=round(event.camera_trust_score, 1),
            )
        )

    items.sort(
        key=lambda item: comparable_timestamp(item.timestamp, assumed_timezone)
    )
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
