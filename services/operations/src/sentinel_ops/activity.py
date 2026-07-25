"""Live activity log: a visible record of what the system writes and where.

Judges asked to see database interaction happening in real time. This keeps a
small in-memory ring buffer of datastore operations - DynamoDB puts, S3 object
writes, SQLite fallbacks - which the dashboard polls and renders as a live feed.

Nothing here is on a critical path. Recording an entry cannot raise, and the
buffer is capped so a long demo cannot exhaust memory.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque

MAX_ENTRIES = 400

_lock = threading.Lock()
_entries: Deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_sequence = 0


def record(
    action: str,
    backend: str,
    target: str,
    detail: str = "",
    status: str = "ok",
    latency_ms: float | None = None,
) -> None:
    """Append one datastore operation to the live feed.

    action   - PUT_ITEM, PUT_OBJECT, QUERY, DELETE...
    backend  - dynamodb, s3, sqlite, local
    target   - table or bucket name
    detail   - short human-readable key or summary (never personal data)
    """
    global _sequence
    try:
        with _lock:
            _sequence += 1
            _entries.append(
                {
                    "seq": _sequence,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "action": action,
                    "backend": backend,
                    "target": target,
                    "detail": detail[:160],
                    "status": status,
                    "latency_ms": round(latency_ms, 1) if latency_ms else None,
                }
            )
    except Exception:  # noqa: BLE001 - telemetry must never break a request
        pass


def since(seq: int = 0, limit: int = 100) -> dict[str, Any]:
    """Return entries newer than `seq`, oldest first."""
    with _lock:
        rows = [entry for entry in _entries if entry["seq"] > seq][-limit:]
        latest = _sequence
        counts: dict[str, int] = {}
        for entry in _entries:
            counts[entry["backend"]] = counts.get(entry["backend"], 0) + 1
    return {
        "entries": rows,
        "latest_seq": latest,
        "buffered": len(rows),
        "totals_by_backend": counts,
    }


def clear() -> None:
    global _sequence
    with _lock:
        _entries.clear()
        _sequence = 0
