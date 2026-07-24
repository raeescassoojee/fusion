from __future__ import annotations

from pathlib import Path

import numpy as np
from rapidfuzz.fuzz import ratio

from .schemas import CameraEvent, ComparisonResult, MatchDecision


def normalize_plate(text: str | None) -> str:
    if not text:
        return ""
    return "".join(character for character in text.upper() if character.isalnum())


def _strength(score: float, available: bool = True) -> str:
    if not available:
        return "NONE"
    if score >= 0.88:
        return "HIGH"
    if score >= 0.68:
        return "MEDIUM"
    return "LOW"


def _exact_text_similarity(a: str, b: str) -> float:
    if (
        not a
        or not b
        or a.casefold() == "unknown"
        or b.casefold() == "unknown"
    ):
        return 0.0
    return 1.0 if a.casefold() == b.casefold() else 0.0


def _load_embedding(reference: str | None, base_dir: Path | None) -> np.ndarray | None:
    if not reference or base_dir is None:
        return None
    path = Path(reference)
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists() or path.suffix.lower() != ".npy":
        return None
    vector = np.load(path).astype(np.float32).reshape(-1)
    return vector if vector.size else None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return 0.0
    cosine = float(np.dot(a, b) / denominator)
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def compare_events(
    event_a: CameraEvent,
    event_b: CameraEvent,
    base_dir_a: str | Path | None = None,
    base_dir_b: str | Path | None = None,
) -> ComparisonResult:
    warnings: list[str] = []
    plate_a = normalize_plate(event_a.plate.text)
    plate_b = normalize_plate(event_b.plate.text)
    both_plates = bool(plate_a and plate_b)
    plate_similarity = ratio(plate_a, plate_b) / 100 if both_plates else 0.0
    colour_similarity = _exact_text_similarity(event_a.vehicle.colour, event_b.vehicle.colour)
    type_similarity = _exact_text_similarity(event_a.vehicle.type, event_b.vehicle.type)

    if both_plates:
        vehicle_score = (
            0.65 * plate_similarity
            + 0.20 * colour_similarity
            + 0.15 * type_similarity
        )
        vehicle_reasons = [
            f"plate similarity {plate_similarity:.2f}",
            f"vehicle colour {'matches' if colour_similarity else 'differs'}",
            f"vehicle type {'matches' if type_similarity else 'differs'}",
        ]
        vehicle_value = vehicle_score >= 0.76
    else:
        vehicle_score = 0.55 * colour_similarity + 0.45 * type_similarity
        vehicle_reasons = [
            "one or both plates unavailable",
            f"vehicle colour {'matches' if colour_similarity else 'differs'}",
            f"vehicle type {'matches' if type_similarity else 'differs'}",
        ]
        vehicle_value = vehicle_score >= 0.90
        warnings.append("vehicle result has weak evidence because a plate is missing")

    embedding_a = _load_embedding(
        event_a.face.embedding_ref, Path(base_dir_a) if base_dir_a else None
    )
    embedding_b = _load_embedding(
        event_b.face.embedding_ref, Path(base_dir_b) if base_dir_b else None
    )
    face_available = embedding_a is not None and embedding_b is not None
    if face_available:
        face_score = cosine_similarity(embedding_a, embedding_b)
        minimum_trust = min(event_a.camera_trust_score, event_b.camera_trust_score)
        face_value = face_score >= 0.78 and minimum_trust >= 55
        face_reasons = [
            f"anonymous embedding similarity {face_score:.2f}",
            f"minimum camera trust {minimum_trust}/100",
        ]
    else:
        face_score = 0.0
        face_value = False
        face_reasons = ["one or both anonymous face embeddings unavailable"]

    upper = _exact_text_similarity(
        event_a.appearance.upper_colour, event_b.appearance.upper_colour
    )
    lower = _exact_text_similarity(
        event_a.appearance.lower_colour, event_b.appearance.lower_colour
    )
    cap_available = (
        event_a.appearance.cap is not None and event_b.appearance.cap is not None
    )
    backpack_available = (
        event_a.appearance.backpack is not None
        and event_b.appearance.backpack is not None
    )
    cap = (
        1.0 if cap_available and event_a.appearance.cap == event_b.appearance.cap else 0.0
    )
    backpack = (
        1.0
        if backpack_available
        and event_a.appearance.backpack == event_b.appearance.backpack
        else 0.0
    )
    weights = [(upper, 0.45), (lower, 0.35)]
    if cap_available:
        weights.append((cap, 0.10))
    if backpack_available:
        weights.append((backpack, 0.10))
    total_weight = sum(weight for _, weight in weights)
    appearance_score = (
        sum(value * weight for value, weight in weights) / total_weight
        if total_weight
        else 0.0
    )
    appearance_available = not (
        event_a.appearance.upper_colour == "Unknown"
        and event_b.appearance.upper_colour == "Unknown"
        and event_a.appearance.lower_colour == "Unknown"
        and event_b.appearance.lower_colour == "Unknown"
    )
    appearance_value = appearance_available and appearance_score >= 0.75
    appearance_reasons = [
        f"upper clothing {'matches' if upper else 'differs or unknown'}",
        f"lower clothing {'matches' if lower else 'differs or unknown'}",
    ]

    return ComparisonResult(
        event_a=event_a.event_id,
        event_b=event_b.event_id,
        possible_same_vehicle=MatchDecision(
            value=vehicle_value,
            score=round(vehicle_score, 3),
            evidence_strength=_strength(vehicle_score, bool(both_plates or colour_similarity or type_similarity)),
            reasons=vehicle_reasons,
        ),
        possible_same_face=MatchDecision(
            value=face_value,
            score=round(face_score, 3),
            evidence_strength=_strength(face_score, face_available),
            reasons=face_reasons,
        ),
        possible_same_appearance=MatchDecision(
            value=appearance_value,
            score=round(appearance_score, 3),
            evidence_strength=_strength(appearance_score, appearance_available),
            reasons=appearance_reasons,
        ),
        warnings=warnings,
    )
