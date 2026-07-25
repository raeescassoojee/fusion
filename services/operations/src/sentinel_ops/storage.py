from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from sentinel_ops.models import Alert, CameraEvent, Claim


OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = OPERATIONS_ROOT / "data" / "sentinel_ops.db"


def database_path() -> Path:
    path = Path(os.getenv("SENTINEL_DATABASE_PATH", str(DEFAULT_DB)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def initialise() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                incident_time TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                priority TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def save_event(event: CameraEvent) -> None:
    initialise()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO events(event_id, timestamp, camera_id, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                timestamp=excluded.timestamp,
                camera_id=excluded.camera_id,
                payload=excluded.payload
            """,
            (
                event.event_id,
                event.timestamp.isoformat(),
                event.camera_id,
                event.model_dump_json(),
            ),
        )


def list_events(limit: int = 200) -> list[CameraEvent]:
    initialise()
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [CameraEvent.model_validate_json(row["payload"]) for row in rows]


def save_claim(claim: Claim) -> None:
    initialise()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO claims(claim_id, incident_time, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                incident_time=excluded.incident_time,
                payload=excluded.payload
            """,
            (
                claim.claim_id,
                claim.incident_time.isoformat(),
                claim.model_dump_json(),
            ),
        )


def list_claims(limit: int = 100) -> list[Claim]:
    initialise()
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM claims ORDER BY incident_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [Claim.model_validate_json(row["payload"]) for row in rows]


def save_alert(alert: Alert) -> None:
    initialise()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO alerts(alert_id, event_id, priority, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(alert_id) DO UPDATE SET
                event_id=excluded.event_id,
                priority=excluded.priority,
                payload=excluded.payload
            """,
            (
                alert.alert_id,
                alert.event_id,
                alert.priority,
                alert.model_dump_json(),
            ),
        )


def list_alerts(limit: int = 100) -> list[Alert]:
    initialise()
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM alerts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [Alert.model_validate_json(row["payload"]) for row in rows]


def status() -> dict[str, Any]:
    initialise()
    with connect() as connection:
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) AS total FROM {table}"  # noqa: S608
            ).fetchone()["total"]
            for table in ("events", "claims", "alerts")
        }
    return {"database": str(database_path()), **counts}
