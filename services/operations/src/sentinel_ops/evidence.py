from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

from sentinel_ops.geo import haversine_km
from sentinel_ops.models import CameraEvent, EvidenceLink
from sentinel_ops.trust_policy import evidence_policy_for_trust


def _norm(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _text_similarity(first: str | None, second: str | None) -> float:
    first_norm = _norm(first)
    second_norm = _norm(second)
    if not first_norm or not second_norm:
        return 0.0
    return SequenceMatcher(None, first_norm, second_norm).ratio()


def _same(first: str | None, second: str | None) -> float:
    return float(
        bool(
            first
            and second
            and first.strip().lower() == second.strip().lower()
        )
    )


def _cosine(first: list[float] | None, second: list[float] | None) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(x * y for x, y in zip(first, second))
    norm_first = math.sqrt(sum(x * x for x in first))
    norm_second = math.sqrt(sum(y * y for y in second))
    if not norm_first or not norm_second:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_first * norm_second)))


def compare_events(first: CameraEvent, second: CameraEvent) -> EvidenceLink:
    if first.event_id == second.event_id:
        raise ValueError("Cannot compare an event with itself")

    plate_similarity = _text_similarity(first.plate.text, second.plate.text)
    plate = (
        40
        * plate_similarity
        * min(first.plate.confidence, second.plate.confidence)
    )

    face_similarity = _cosine(first.face.embedding, second.face.embedding)
    if not face_similarity:
        face_similarity = _same(
            first.face.reference_token,
            second.face.reference_token,
        )
    face_confidence = min(first.face.confidence, second.face.confidence)
    raw_face = 25 * face_similarity * max(
        face_confidence,
        0.5 if face_similarity else 0,
    )
    trust_floor = min(first.camera_trust_score, second.camera_trust_score)
    policy = evidence_policy_for_trust(trust_floor)
    face = raw_face if policy["biometric_escalation_enabled"] else 0.0

    vehicle_similarity = (
        0.45 * _same(first.vehicle.colour, second.vehicle.colour)
        + 0.35 * _same(first.vehicle.type, second.vehicle.type)
        + 0.10 * _same(first.vehicle.make, second.vehicle.make)
        + 0.10 * _same(first.vehicle.model, second.vehicle.model)
    )
    vehicle = 15 * vehicle_similarity

    appearance_similarity = (
        0.4
        * _same(
            first.appearance.upper_colour,
            second.appearance.upper_colour,
        )
        + 0.4
        * _same(
            first.appearance.lower_colour,
            second.appearance.lower_colour,
        )
        + 0.2
        * _same(
            first.appearance.descriptor_token,
            second.appearance.descriptor_token,
        )
    )
    appearance = 10 * appearance_similarity

    distance = haversine_km(first.location, second.location)
    minutes = abs((second.timestamp - first.timestamp).total_seconds()) / 60
    journey_plausible = minutes + 2 >= 60 * distance / 120
    journey = 10.0 if journey_plausible else 0.0

    trust = (first.camera_trust_score + second.camera_trust_score) / 200
    score = (
        0.0
        if policy["metadata_only"]
        else round(
            (plate + face + vehicle + appearance + journey)
            * (0.65 + 0.35 * trust),
            1,
        )
    )

    reasons: list[str] = []
    if plate >= 24:
        reasons.append("Strong plate agreement")
    elif plate >= 12:
        reasons.append("Partial plate agreement")
    if face >= 15:
        reasons.append("Face signal supports human review")
    elif raw_face > 0 and not policy["biometric_escalation_enabled"]:
        reasons.append(
            f"Face signal retained as metadata but disabled by the {policy['band']} trust gate"
        )
    if vehicle >= 9:
        reasons.append("Vehicle attributes agree")
    if appearance >= 6:
        reasons.append("Visible appearance attributes agree")
    reasons.append(
        "Journey is geographically plausible"
        if journey_plausible
        else "Journey timing is implausible"
    )
    if trust_floor < 85:
        reasons.append(f"Trust policy: {policy['label']}")

    if policy["metadata_only"]:
        relationship = "WEAK_CONNECTION"
    elif score >= 75:
        relationship = "HIGH_PRIORITY_REVIEW"
    elif plate >= 18 or vehicle >= 10:
        relationship = "POSSIBLE_SAME_VEHICLE"
    elif face >= 10 or appearance >= 6:
        relationship = "POSSIBLE_SAME_APPEARANCE"
    else:
        relationship = "WEAK_CONNECTION"

    return EvidenceLink(
        first_event_id=first.event_id,
        second_event_id=second.event_id,
        score=score,
        relationship=relationship,
        components={
            "plate": round(plate, 1),
            "face": round(face, 1),
            "face_raw_metadata": round(raw_face, 1),
            "vehicle": round(vehicle, 1),
            "appearance": round(appearance, 1),
            "journey": journey,
            "camera_trust": round(trust * 100, 1),
            "trust_floor": round(trust_floor, 1),
            "biometric_escalation_enabled": float(policy["biometric_escalation_enabled"]),
            "alert_enabled": float(policy["alert_enabled"]),
        },
        reasons=reasons or ["No strong relationship found"],
        journey_distance_km=round(distance, 3),
        journey_plausible=journey_plausible,
    )
