"""Small in-process benchmark ledger for the live judging dashboard.

Only measured request durations are exposed.  The ledger is intentionally local:
it adds no network dependency to the camera hot path and resets between rehearsals.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import UTC, datetime, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

_LOCK = threading.RLock()
_SAMPLES: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
_STARTED_AT = datetime.now(UTC).isoformat()
CLIENT_METRICS = {
    "identity_roundtrip_ms",
    "ocr_roundtrip_ms",
    "cross_camera_roundtrip_ms",
    "alert_roundtrip_ms",
}


class ClientMetric(BaseModel):
    name: str
    milliseconds: float = Field(ge=0, le=120_000)


def record_metric(name: str, milliseconds: float) -> None:
    value = float(milliseconds)
    if value < 0 or not np.isfinite(value):
        return
    with _LOCK:
        _SAMPLES[name].append(round(value, 3))


def record_client_metric(metric: ClientMetric) -> None:
    if metric.name not in CLIENT_METRICS:
        raise ValueError(f"Unsupported client metric: {metric.name}")
    record_metric(metric.name, metric.milliseconds)


def reset_metrics() -> None:
    global _STARTED_AT
    with _LOCK:
        _SAMPLES.clear()
        _STARTED_AT = datetime.now(UTC).isoformat()


def performance_snapshot() -> dict[str, Any]:
    with _LOCK:
        rows = {name: list(values) for name, values in _SAMPLES.items()}
        started_at = _STARTED_AT
    metrics: dict[str, Any] = {}
    for name, values in sorted(rows.items()):
        array = np.asarray(values, dtype=np.float64)
        metrics[name] = {
            "runs": int(array.size),
            "latest_ms": round(float(array[-1]), 1),
            "p50_ms": round(float(np.percentile(array, 50)), 1),
            "p95_ms": round(float(np.percentile(array, 95)), 1),
            "min_ms": round(float(array.min()), 1),
            "max_ms": round(float(array.max()), 1),
        }
    return {
        "started_at": started_at,
        "metrics": metrics,
        "method": "Last 200 measured server requests; p50 and p95 are calculated, not seeded.",
        "targets": {
            "face_detection_ms": {"p50": 250, "p95": 500},
            "face_recognition_ms": {"p50": 700, "p95": 1200},
            "face_batch_ms": {"three_people_p95": 2000},
            "identity_roundtrip_ms": {"p50": 1000, "p95": 1800},
        },
    }
