from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import TrustConfig
from .schemas import BoundingBox, QualityMetrics


@dataclass(slots=True)
class TrustResult:
    score: int
    metrics: QualityMetrics
    reasons: list[str]
    raw: dict[str, float]


def evidence_policy_for_trust(score: float | int) -> dict[str, object]:
    """Map a camera trust score to the finals P1 evidence gate."""
    trust = max(0.0, min(100.0, float(score)))
    if trust >= 85:
        band, label = "STRONG", "All evidence enabled"
        height, biometric, alert, metadata, disabled = True, True, True, False, []
    elif trust >= 70:
        band, label = "USABLE", "Height disabled"
        height, biometric, alert, metadata = False, True, True, False
        disabled = ["HEIGHT_ESTIMATION"]
    elif trust >= 50:
        band, label = "WEAK", "Biometric escalation disabled"
        height, biometric, alert, metadata = False, False, True, False
        disabled = ["HEIGHT_ESTIMATION", "BIOMETRIC_ESCALATION"]
    else:
        band, label = "METADATA_ONLY", "Metadata only — alerts blocked"
        height, biometric, alert, metadata = False, False, False, True
        disabled = ["HEIGHT_ESTIMATION", "BIOMETRIC_ESCALATION", "ALERT_ESCALATION"]
    return {
        "trust_score": round(trust, 1),
        "band": band,
        "label": label,
        "height_enabled": height,
        "biometric_escalation_enabled": biometric,
        "alert_enabled": alert,
        "metadata_only": metadata,
        "disabled_evidence": disabled,
        "raw_evidence_retained": True,
        "human_review_required": True,
    }


def sharpness_score(frame: np.ndarray) -> tuple[int, float]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    score = int(round(min(100.0, max(0.0, 25.0 * math.log10(variance + 1.0)))))
    return score, variance


def lighting_score(frame: np.ndarray) -> tuple[int, float]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    if 70 <= mean <= 190:
        score = 100
    elif mean < 70:
        score = int(round(max(0.0, mean / 70 * 100)))
    else:
        score = int(round(max(0.0, (255 - mean) / 65 * 100)))
    return min(100, score), mean


def resolution_score(frame: np.ndarray, evidence_box: BoundingBox | None = None) -> int:
    if evidence_box:
        pixels = evidence_box.width * evidence_box.height
        target = 180 * 100
    else:
        height, width = frame.shape[:2]
        pixels = width * height
        target = 1280 * 720
    return int(round(min(100.0, max(0.0, pixels / target * 100))))


def unobstructed_score(frame: np.ndarray, box: BoundingBox | None) -> int:
    if box is None:
        return 55
    height, width = frame.shape[:2]
    edge_margin_x = max(4, int(width * 0.01))
    edge_margin_y = max(4, int(height * 0.01))
    touches = sum(
        [
            box.x <= edge_margin_x,
            box.y <= edge_margin_y,
            box.x2 >= width - edge_margin_x,
            box.y2 >= height - edge_margin_y,
        ]
    )
    if touches == 0:
        return 100
    if touches == 1:
        return 72
    if touches == 2:
        return 45
    return 20


def calculate_trust(
    frame: np.ndarray,
    detection_confidences: list[float],
    evidence_box: BoundingBox | None,
    config: TrustConfig,
) -> TrustResult:
    sharpness, laplacian_variance = sharpness_score(frame)
    lighting, mean_brightness = lighting_score(frame)
    detection = int(
        round(100 * (sum(detection_confidences) / len(detection_confidences)))
    ) if detection_confidences else 20
    detection = max(0, min(100, detection))
    unobstructed = unobstructed_score(frame, evidence_box)
    resolution = resolution_score(frame, evidence_box)

    metrics = QualityMetrics(
        sharpness=sharpness,
        lighting=lighting,
        detection=detection,
        unobstructed=unobstructed,
        resolution=resolution,
    )
    weights = config.normalized_weights()
    score = int(
        round(
            sharpness * weights["sharpness"]
            + lighting * weights["lighting"]
            + detection * weights["detection"]
            + unobstructed * weights["unobstructed"]
            + resolution * weights["resolution"]
        )
    )
    reasons: list[str] = []
    reasons.append(
        "clear image"
        if sharpness >= 70
        else "moderate motion blur"
        if sharpness >= 45
        else "severe blur"
    )
    reasons.append(
        "good lighting"
        if lighting >= 80
        else "moderate lighting"
        if lighting >= 50
        else "poor lighting"
    )
    reasons.append(
        "strong detections"
        if detection >= 75
        else "moderate detections"
        if detection >= 45
        else "weak detections"
    )
    if unobstructed < 80:
        reasons.append("target partly clipped or obstructed")
    if resolution < 60:
        reasons.append("low evidence resolution")
    return TrustResult(
        score=max(0, min(100, score)),
        metrics=metrics,
        reasons=reasons,
        raw={
            "laplacian_variance": round(laplacian_variance, 2),
            "mean_brightness": round(mean_brightness, 2),
        },
    )
