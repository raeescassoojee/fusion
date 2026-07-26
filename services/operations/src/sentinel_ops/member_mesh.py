"""Persistent Member camera mesh, visitor profiles and reviewed incident continuity.

The hackathon build uses one local SQLite database so registrations, anonymous face
profiles, camera sightings, classifications, calibrations and incident watches survive
restarts without AWS credentials. Every material write is also copied into an outbox
that can later be drained to DynamoDB/S3 by the existing AWS layer.

Face similarity creates a *repeat visitor candidate*. It never identifies a person or
proves wrongdoing. Trusted/cleared/intruder decisions are explicit human reviews and
trusted status is scoped to one household unless an incident is confirmed.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from sentinel_ops.claims_bridge import load_claims_hotspots
from sentinel_ops.height import (
    CameraCalibration,
    HeightUnavailable,
    estimate_height,
    observations_from_person_boxes,
)
from sentinel_ops.roles_api import _suburb_stats
from sentinel_ops.storage import connect, database_path

router = APIRouter(tags=["member mesh"])

OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = OPERATIONS_ROOT.parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"
FACE_MEDIA_ROOT = OPERATIONS_ROOT / "data" / "member_faces"
FACE_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

FACE_MATCH_THRESHOLD = float(os.getenv("SENTINEL_FACE_MATCH_THRESHOLD", "0.72"))
MAX_FACE_UPLOAD = 5 * 1024 * 1024
MAX_PERSON_SPEED_KMH = 25.0
ALLOWED_PROFILE_STATUSES = {
    "UNKNOWN",
    "TRUSTED",
    "REVIEW_REQUIRED",
    "CONFIRMED_INTRUDER",
    "CLEARED",
}
MEMBER_TABLES = (
    "member_users",
    "member_cameras",
    "member_camera_calibrations",
    "face_profiles",
    "face_sightings",
    "member_profile_labels",
    "member_incidents",
    "member_camera_notifications",
    "member_alert_events",
    "member_audit_log",
    "aws_sync_outbox",
)
AWS_TABLE_MAP = {
    "member_users": "SentinelMemberUsers",
    "member_cameras": "SentinelCameras",
    "member_camera_calibrations": "SentinelCameraCalibration",
    "face_profiles": "SentinelAnonymousProfiles",
    "face_sightings": "SentinelSightings",
    "member_profile_labels": "SentinelProfileReviews",
    "member_incidents": "SentinelIncidents",
    "member_camera_notifications": "SentinelCameraNotifications",
    "member_alert_events": "MzansiMeshMemberAlerts",
    "member_audit_log": "SentinelAuditLog",
    "face media": "S3 sentinel-evidence/member-faces/",
}

# Close, deliberately legible demo coordinates. Normal address entry still tries
# Nominatim when internet is available.
DEMO_USERS = [
    {
        "user_id": "USR-001",
        "display_name": "User 1",
        "member_number": "DISC-1001",
        "household": "17 Sher Avenue",
        "suburb": "Lakefield",
        "metro": "Gauteng",
        "latitude": -26.198020,
        "longitude": 28.310300,
    },
    {
        "user_id": "USR-002",
        "display_name": "User 2",
        "member_number": "DISC-1002",
        "household": "18 Sher Avenue",
        "suburb": "Lakefield",
        "metro": "Gauteng",
        "latitude": -26.198090,
        "longitude": 28.310420,
    },
    {
        "user_id": "USR-003",
        "display_name": "User 3",
        "member_number": "DISC-1003",
        "household": "19 Sher Avenue",
        "suburb": "Lakefield",
        "metro": "Gauteng",
        "latitude": -26.198160,
        "longitude": 28.310540,
    },
]
LOCAL_GEOCODES = {
    "17 sher avenue": DEMO_USERS[0],
    "17 sher ave": DEMO_USERS[0],
    "18 sher avenue": DEMO_USERS[1],
    "18 sher ave": DEMO_USERS[1],
    "19 sher avenue": DEMO_USERS[2],
    "19 sher ave": DEMO_USERS[2],
    "lakefield": DEMO_USERS[0],
}


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _now() -> str:
    return _now_dt().isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_obj(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _ensure_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _risk_context(metro: str, suburb: str) -> tuple[str, str | None, float | None]:
    try:
        hotspots, _ = load_claims_hotspots(metro)
        for hotspot in hotspots:
            if hotspot.name.strip().lower() == suburb.strip().lower():
                risk = float(
                    getattr(hotspot, "operational_priority", None)
                    or getattr(hotspot, "risk_score", 0)
                    or 0
                )
                return ("HEIGHTENED" if risk >= 60 else "NORMAL", hotspot.hotspot_id, round(risk, 1))
    except Exception:
        pass
    return "NORMAL", None, None


def initialise_member_store() -> None:
    """Create/migrate the local Member schema and seed the three demo homes."""
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS member_users (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                member_number TEXT NOT NULL,
                household TEXT NOT NULL,
                suburb TEXT NOT NULL,
                metro TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS member_cameras (
                camera_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                household TEXT NOT NULL,
                suburb TEXT NOT NULL,
                metro TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                device_label TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                hotspot_id TEXT,
                geofence_risk REAL,
                registered_at TEXT NOT NULL,
                camera_trust REAL NOT NULL DEFAULT 100,
                last_seen_at TEXT,
                FOREIGN KEY(user_id) REFERENCES member_users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_member_cameras_user ON member_cameras(user_id);
            CREATE TABLE IF NOT EXISTS member_camera_calibrations (
                camera_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                image_width INTEGER NOT NULL,
                image_height INTEGER NOT NULL,
                mount_height_m REAL,
                tilt_deg REAL,
                horizontal_fov_deg REAL,
                horizon_y REAL,
                ref_height_m REAL,
                ref_foot_y REAL,
                ref_head_y REAL,
                calibration_score REAL NOT NULL DEFAULT 100,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                FOREIGN KEY(camera_id) REFERENCES member_cameras(camera_id)
            );
            CREATE TABLE IF NOT EXISTS face_profiles (
                profile_id TEXT PRIMARY KEY,
                anonymous_label TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_size INTEGER NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                sighting_count INTEGER NOT NULL DEFAULT 1,
                system_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                review_required INTEGER NOT NULL DEFAULT 0,
                last_classified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS face_sightings (
                sighting_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                similarity REAL NOT NULL,
                detection_confidence REAL,
                media_name TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                embedding BLOB,
                embedding_size INTEGER,
                height_low_m REAL,
                height_high_m REAL,
                height_point_m REAL,
                height_quality REAL,
                height_method TEXT,
                height_status TEXT,
                upper_colour TEXT,
                lower_colour TEXT,
                appearance_confidence REAL,
                headwear TEXT,
                carried_item TEXT,
                person_box_json TEXT,
                match_breakdown_json TEXT,
                journey_distance_m REAL,
                journey_speed_kmh REAL,
                journey_direction TEXT,
                review_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
                FOREIGN KEY(profile_id) REFERENCES face_profiles(profile_id),
                FOREIGN KEY(user_id) REFERENCES member_users(user_id),
                FOREIGN KEY(camera_id) REFERENCES member_cameras(camera_id)
            );
            CREATE INDEX IF NOT EXISTS idx_face_sightings_profile ON face_sightings(profile_id, captured_at);
            CREATE INDEX IF NOT EXISTS idx_face_sightings_user ON face_sightings(user_id, captured_at);
            CREATE TABLE IF NOT EXISTS member_profile_labels (
                profile_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                display_label TEXT,
                category TEXT,
                valid_until TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                PRIMARY KEY(profile_id, user_id),
                FOREIGN KEY(profile_id) REFERENCES face_profiles(profile_id),
                FOREIGN KEY(user_id) REFERENCES member_users(user_id)
            );
            CREATE TABLE IF NOT EXISTS member_incidents (
                incident_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                origin_user_id TEXT NOT NULL,
                origin_camera_id TEXT NOT NULL,
                origin_sighting_id TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL DEFAULT 30,
                expires_at TEXT,
                notes TEXT,
                confirmed_by TEXT,
                ended_at TEXT,
                outcome TEXT,
                FOREIGN KEY(profile_id) REFERENCES face_profiles(profile_id)
            );
            CREATE INDEX IF NOT EXISTS idx_member_incidents_profile ON member_incidents(profile_id, status, started_at);
            CREATE TABLE IF NOT EXISTS member_camera_notifications (
                notification_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                captured_sighting_id TEXT,
                viewed_at TEXT,
                UNIQUE(incident_id, camera_id),
                FOREIGN KEY(incident_id) REFERENCES member_incidents(incident_id),
                FOREIGN KEY(camera_id) REFERENCES member_cameras(camera_id)
            );
            CREATE TABLE IF NOT EXISTS member_alert_events (
                alert_id TEXT PRIMARY KEY,
                dedupe_key TEXT NOT NULL,
                user_id TEXT NOT NULL,
                incident_id TEXT,
                profile_id TEXT,
                sighting_id TEXT,
                alert_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                urgent INTEGER NOT NULL DEFAULT 1,
                channels_json TEXT NOT NULL DEFAULT '[]',
                action TEXT,
                context_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                read_at TEXT,
                UNIQUE(dedupe_key, user_id),
                FOREIGN KEY(user_id) REFERENCES member_users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_member_alert_events_user_created
                ON member_alert_events(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS member_audit_log (
                audit_id TEXT PRIMARY KEY,
                table_name TEXT NOT NULL,
                record_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_member_audit_created ON member_audit_log(created_at DESC);
            CREATE TABLE IF NOT EXISTS aws_sync_outbox (
                outbox_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'LOCAL_PENDING',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                synced_at TEXT,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status ON aws_sync_outbox(status, created_at);
            """
        )
        # Migrate Phase 1/2 databases without deleting user data.
        for definition in (
            "camera_trust REAL NOT NULL DEFAULT 100",
            "last_seen_at TEXT",
        ):
            _ensure_column(db, "member_cameras", definition)
        for definition in (
            "system_status TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "review_required INTEGER NOT NULL DEFAULT 0",
            "last_classified_at TEXT",
        ):
            _ensure_column(db, "face_profiles", definition)
        for definition in (
            "embedding BLOB",
            "embedding_size INTEGER",
            "height_low_m REAL",
            "height_high_m REAL",
            "height_point_m REAL",
            "height_quality REAL",
            "height_method TEXT",
            "height_status TEXT",
            "upper_colour TEXT",
            "lower_colour TEXT",
            "appearance_confidence REAL",
            "headwear TEXT",
            "carried_item TEXT",
            "person_box_json TEXT",
            "match_breakdown_json TEXT",
            "journey_distance_m REAL",
            "journey_speed_kmh REAL",
            "journey_direction TEXT",
            "review_status TEXT NOT NULL DEFAULT 'UNREVIEWED'",
        ):
            _ensure_column(db, "face_sightings", definition)
        for definition in (
            "duration_minutes INTEGER NOT NULL DEFAULT 30",
            "expires_at TEXT",
            "notes TEXT",
            "confirmed_by TEXT",
            "ended_at TEXT",
            "outcome TEXT",
        ):
            _ensure_column(db, "member_incidents", definition)
        _ensure_column(db, "member_camera_notifications", "viewed_at TEXT")

        for user in DEMO_USERS:
            db.execute(
                """
                INSERT INTO member_users(
                    user_id, display_name, member_number, household, suburb, metro,
                    latitude, longitude, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    member_number=excluded.member_number,
                    household=excluded.household,
                    suburb=excluded.suburb,
                    metro=excluded.metro,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude
                """,
                (
                    user["user_id"], user["display_name"], user["member_number"],
                    user["household"], user["suburb"], user["metro"],
                    user["latitude"], user["longitude"], _now(),
                ),
            )
            camera_id = f"CAM-U{user['user_id'][-1]}-01"
            mode, hotspot_id, risk = _risk_context(user["metro"], user["suburb"])
            db.execute(
                """
                INSERT INTO member_cameras(
                    camera_id, user_id, household, suburb, metro, latitude, longitude,
                    device_label, status, mode, hotspot_id, geofence_risk, registered_at,
                    camera_trust
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100)
                ON CONFLICT(camera_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    household=excluded.household,
                    suburb=excluded.suburb,
                    metro=excluded.metro,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    device_label=excluded.device_label,
                    mode=excluded.mode,
                    hotspot_id=excluded.hotspot_id,
                    geofence_risk=excluded.geofence_risk
                """,
                (
                    camera_id, user["user_id"], user["household"], user["suburb"],
                    user["metro"], user["latitude"], user["longitude"],
                    "Laptop / doorbell demo camera", "READY_FOR_LIVE_FEED", mode,
                    hotspot_id, risk, _now(),
                ),
            )


def _audit(
    db: sqlite3.Connection,
    table_name: str,
    record_id: str,
    action: str,
    actor: str,
    summary: str,
    payload: Any | None = None,
    *,
    queue_for_aws: bool = True,
) -> None:
    created_at = _now()
    payload_json = _safe_json(payload) if payload is not None else None
    db.execute(
        """
        INSERT INTO member_audit_log(
            audit_id, table_name, record_id, action, actor, summary, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"AUD-{uuid.uuid4().hex[:12].upper()}", table_name, record_id,
            action, actor, summary, payload_json, created_at,
        ),
    )
    if queue_for_aws and table_name in AWS_TABLE_MAP:
        db.execute(
            """
            INSERT INTO aws_sync_outbox(
                outbox_id, entity_type, entity_id, action, payload_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'LOCAL_PENDING', ?)
            """,
            (
                f"OUT-{uuid.uuid4().hex[:12].upper()}", table_name, record_id,
                action, payload_json or "{}", created_at,
            ),
        )


def _create_member_alert(
    db: sqlite3.Connection,
    *,
    dedupe_key: str,
    user_id: str,
    alert_type: str,
    title: str,
    body: str,
    incident_id: str | None = None,
    profile_id: str | None = None,
    sighting_id: str | None = None,
    urgent: bool = True,
    channels: list[str] | None = None,
    action: str | None = "member-trail",
    context: dict[str, Any] | None = None,
) -> str | None:
    alert_id = f"MAL-{uuid.uuid4().hex[:11].upper()}"
    created_at = _now()
    cursor = db.execute(
        """
        INSERT OR IGNORE INTO member_alert_events(
            alert_id, dedupe_key, user_id, incident_id, profile_id, sighting_id,
            alert_type, title, body, urgent, channels_json, action, context_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert_id, dedupe_key, user_id, incident_id, profile_id, sighting_id,
            alert_type, title, body, int(urgent), _safe_json(channels or ["IN-APP"]),
            action, _safe_json(context or {}), created_at,
        ),
    )
    if cursor.rowcount == 0:
        return None
    _audit(
        db, "member_alert_events", alert_id, "INSERT", "MzansiMesh notifications",
        title,
        {
            "dedupe_key": dedupe_key,
            "user_id": user_id,
            "incident_id": incident_id,
            "profile_id": profile_id,
            "sighting_id": sighting_id,
            "channels": channels or ["IN-APP"],
        },
    )
    return alert_id


def _get_user(user_id: str) -> dict[str, Any]:
    initialise_member_store()
    with connect() as db:
        user = _row(db.execute("SELECT * FROM member_users WHERE user_id=?", (user_id,)).fetchone())
    if not user:
        raise HTTPException(status_code=404, detail="member not found")
    return user


def _get_camera(camera_id: str, user_id: str | None = None) -> dict[str, Any]:
    initialise_member_store()
    query = "SELECT * FROM member_cameras WHERE camera_id=?"
    args: tuple[Any, ...] = (camera_id,)
    if user_id:
        query += " AND user_id=?"
        args = (camera_id, user_id)
    with connect() as db:
        camera = _row(db.execute(query, args).fetchone())
    if not camera:
        raise HTTPException(status_code=404, detail="camera not found for this member")
    return camera


class CameraRegistration(BaseModel):
    user_id: str
    household: str = Field(..., min_length=3)
    suburb: str = Field(..., min_length=2)
    metro: str = "Gauteng"
    latitude: float
    longitude: float
    device_label: str = "Doorbell camera"
    consent_acknowledged: bool


class IncidentWatchStart(BaseModel):
    sighting_id: str
    incident_type: str = "TRESPASSING"
    confirmed_by_operator: bool = False
    duration_minutes: int = Field(default=30, ge=5, le=240)
    notes: str = ""
    confirmed_by: str = "Member demo operator"


class IncidentCloseIn(BaseModel):
    outcome: str = "CLOSED_BY_OPERATOR"
    notes: str = ""
    closed_by: str = "Member demo operator"


class ProfileClassificationIn(BaseModel):
    user_id: str
    status: Literal["UNKNOWN", "TRUSTED", "REVIEW_REQUIRED", "CONFIRMED_INTRUDER", "CLEARED"]
    display_label: str | None = None
    category: str | None = None
    valid_until: str | None = None
    notes: str = ""
    updated_by: str = "Member demo operator"
    sighting_id: str | None = None
    start_incident_watch: bool = False
    incident_type: str = "TRESPASSING"
    duration_minutes: int = Field(default=30, ge=5, le=240)


class CalibrationIn(BaseModel):
    mode: Literal["INTRINSIC", "REFERENCE"] = "INTRINSIC"
    image_width: int = Field(..., ge=100)
    image_height: int = Field(..., ge=100)
    mount_height_m: float | None = None
    tilt_deg: float | None = None
    horizontal_fov_deg: float | None = None
    horizon_y: float | None = None
    ref_height_m: float | None = None
    ref_foot_y: float | None = None
    ref_head_y: float | None = None
    calibration_score: float = Field(default=100, ge=0, le=100)
    updated_by: str = "Member demo operator"


class FalseMatchIn(BaseModel):
    reason: str = Field(..., min_length=3)
    reviewer: str = "Member demo operator"


def _distance_metres(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1.0 - h)))


def _direction(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> str:
    dy = b_lat - a_lat
    dx = b_lon - a_lon
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return "STATIONARY"
    angle = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((angle + 22.5) // 45) % 8]


def _height_overlap(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if not a or not b or None in a or None in b:
        return None
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (hi - lo) / max(a[1] - a[0], b[1] - b[0], 1e-6)))


def _profile_status(db: sqlite3.Connection, profile_id: str, viewer_user_id: str | None = None) -> str:
    active = db.execute(
        "SELECT 1 FROM member_incidents WHERE profile_id=? AND status='ACTIVE' LIMIT 1",
        (profile_id,),
    ).fetchone()
    if active:
        return "ACTIVE_INCIDENT"
    intruder = db.execute(
        "SELECT 1 FROM member_profile_labels WHERE profile_id=? AND status='CONFIRMED_INTRUDER' LIMIT 1",
        (profile_id,),
    ).fetchone()
    if intruder:
        return "CONFIRMED_INTRUDER"
    if viewer_user_id:
        local = db.execute(
            "SELECT status FROM member_profile_labels WHERE profile_id=? AND user_id=?",
            (profile_id, viewer_user_id),
        ).fetchone()
        if local and local["status"] != "UNKNOWN":
            return local["status"]
    profile = db.execute(
        "SELECT sighting_count, review_required FROM face_profiles WHERE profile_id=?",
        (profile_id,),
    ).fetchone()
    if profile and profile["review_required"]:
        return "REVIEW_REQUIRED"
    return "REPEAT_VISITOR" if profile and profile["sighting_count"] >= 2 else "UNKNOWN"


def _expire_incidents(db: sqlite3.Connection) -> None:
    now = _now()
    expired = db.execute(
        "SELECT incident_id FROM member_incidents WHERE status='ACTIVE' AND expires_at IS NOT NULL AND expires_at<=?",
        (now,),
    ).fetchall()
    for row in expired:
        db.execute(
            "UPDATE member_incidents SET status='EXPIRED', updated_at=?, ended_at=?, outcome='WATCH_WINDOW_EXPIRED' WHERE incident_id=?",
            (now, now, row["incident_id"]),
        )
        db.execute(
            "UPDATE member_camera_notifications SET status='WATCH_EXPIRED', updated_at=? WHERE incident_id=? AND status='WATCH_ACTIVE'",
            (now, row["incident_id"]),
        )


@router.get("/api/members")
def list_members():
    initialise_member_store()
    with connect() as db:
        users = [dict(row) for row in db.execute("SELECT * FROM member_users ORDER BY user_id").fetchall()]
        for user in users:
            user["camera_count"] = db.execute(
                "SELECT COUNT(*) AS n FROM member_cameras WHERE user_id=?", (user["user_id"],)
            ).fetchone()["n"]
    return {"count": len(users), "users": users, "demo_only": True}


@router.get("/api/member/geocode")
def geocode_address(q: str = Query(..., min_length=3)):
    cleaned = " ".join(q.strip().lower().split())
    for key, item in LOCAL_GEOCODES.items():
        if key in cleaned or cleaned in key:
            return {
                "query": q,
                "source": "demo address index",
                "household": item["household"],
                "suburb": item["suburb"],
                "metro": item["metro"],
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "display_name": f"{item['household']}, {item['suburb']}, South Africa",
            }
    params = urllib.parse.urlencode({
        "format": "jsonv2", "limit": 1, "countrycodes": "za", "addressdetails": 1, "q": q,
    })
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={"User-Agent": "SentinelMesh-GradHack-Demo/0.3"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:  # noqa: S310
            results = json.loads(response.read().decode("utf-8"))
        if results:
            result = results[0]
            address = result.get("address") or {}
            suburb = (
                address.get("suburb") or address.get("neighbourhood")
                or address.get("city_district") or address.get("town") or address.get("city") or ""
            )
            province = (address.get("state") or "").lower()
            metro = "Cape Town" if "western cape" in province else "Gauteng"
            return {
                "query": q,
                "source": "OpenStreetMap Nominatim",
                "household": q.strip(),
                "suburb": suburb,
                "metro": metro,
                "latitude": float(result["lat"]),
                "longitude": float(result["lon"]),
                "display_name": result.get("display_name", q),
            }
    except Exception:
        pass
    raise HTTPException(
        status_code=404,
        detail="Address could not be geocoded. For the offline demo use one of the three preloaded addresses.",
    )


@router.get("/api/member/{user_id}/cameras")
def member_cameras(user_id: str):
    user = _get_user(user_id)
    with connect() as db:
        cameras = [dict(row) for row in db.execute(
            """
            SELECT c.*, CASE WHEN cal.camera_id IS NULL THEN 0 ELSE 1 END AS calibrated
            FROM member_cameras c
            LEFT JOIN member_camera_calibrations cal ON cal.camera_id=c.camera_id
            WHERE c.user_id=? ORDER BY c.registered_at
            """,
            (user_id,),
        ).fetchall()]
    return {"user": user, "count": len(cameras), "cameras": cameras}


@router.post("/api/member/cameras/register")
def register_member_camera(body: CameraRegistration):
    _get_user(body.user_id)
    if not body.consent_acknowledged:
        raise HTTPException(status_code=422, detail="Household consent is required")
    if not (-35 < body.latitude < -22 and 16 < body.longitude < 33):
        raise HTTPException(status_code=422, detail="coordinates fall outside South Africa")
    initialise_member_store()
    with connect() as db:
        seq = db.execute(
            "SELECT COUNT(*) AS n FROM member_cameras WHERE user_id=?", (body.user_id,)
        ).fetchone()["n"] + 1
        camera_id = f"CAM-U{body.user_id[-1]}-{seq:02d}"
        mode, hotspot_id, risk = _risk_context(body.metro, body.suburb)
        registered_at = _now()
        db.execute(
            """
            INSERT INTO member_cameras(
                camera_id, user_id, household, suburb, metro, latitude, longitude,
                device_label, status, mode, hotspot_id, geofence_risk, registered_at, camera_trust
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100)
            """,
            (
                camera_id, body.user_id, body.household.strip(), body.suburb.strip().title(),
                body.metro, body.latitude, body.longitude, body.device_label.strip(),
                "READY_FOR_LIVE_FEED", mode, hotspot_id, risk, registered_at,
            ),
        )
        db.execute(
            "UPDATE member_users SET household=?, suburb=?, metro=?, latitude=?, longitude=? WHERE user_id=?",
            (
                body.household.strip(), body.suburb.strip().title(), body.metro,
                body.latitude, body.longitude, body.user_id,
            ),
        )
        payload = {
            "camera_id": camera_id, "user_id": body.user_id, "household": body.household,
            "latitude": body.latitude, "longitude": body.longitude,
        }
        _audit(db, "member_cameras", camera_id, "INSERT", body.user_id, f"Camera registered at {body.household}", payload)
    return {
        "camera": _get_camera(camera_id, body.user_id),
        "next_steps": [
            "Attach the laptop webcam in My Property.",
            "Calibrate the fixed camera if you want defensible height bands.",
            "Stable scans create anonymous sightings and compare them across the three homes.",
        ],
    }


@router.get("/api/member/{user_id}/summary")
def member_summary(user_id: str):
    user = _get_user(user_id)
    stats = _suburb_stats().get(user["suburb"].strip().title())
    cameras = member_cameras(user_id)["cameras"]
    base = {"user": user, "cameras": cameras, "known": bool(stats)}
    if not stats:
        return {**base, "message": "No claims history for this suburb in the supplied data."}
    return {
        **base,
        "incidents_5y": stats["count"],
        "peak_hours": stats["peak_hours"],
        "peak_days": stats["peak_days"],
        "common_perils": stats["perils"],
        "privacy_note": "This member sees only their property, their camera settings and reviewed mesh evidence.",
    }


@lru_cache(maxsize=1)
def _face_system():
    try:
        from sentinel_camera_ai.config import AppConfig
        from sentinel_camera_ai.detectors.face import FaceSystem
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"face recognition package unavailable: {exc}") from exc
    config = AppConfig.load(CONFIG_PATH)
    system = FaceSystem(config)
    if not system.embedding_enabled:
        raise RuntimeError("OpenCV SFace model could not be loaded")
    return system


@lru_cache(maxsize=1)
def _hog_people_detector():
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def _face_box_iou(a: dict[str, float], b: dict[str, float]) -> float:
    ax1, ay1 = float(a.get("x", 0)), float(a.get("y", 0))
    ax2, ay2 = ax1 + float(a.get("width", 0)), ay1 + float(a.get("height", 0))
    bx1, by1 = float(b.get("x", 0)), float(b.get("y", 0))
    bx2, by2 = bx1 + float(b.get("width", 0)), by1 + float(b.get("height", 0))
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = max(1e-9, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
    return inter / union


def _embedding_from_image(
    image: np.ndarray,
    target_box: dict[str, float] | None = None,
) -> tuple[np.ndarray, float, np.ndarray, dict[str, int]]:
    """Create an embedding for one explicitly selected face.

    A browser frame can contain several people.  Earlier builds embedded the
    largest server-detected face, while the UI painted that result above every
    green box.  When ``target_box`` is supplied we choose the server detection
    that overlaps that browser box, so names and classifications stay attached
    to the correct person.
    """
    from sentinel_camera_ai.detection import Detection
    from sentinel_camera_ai.schemas import BoundingBox

    system = _face_system()
    detections = system.detect(image)
    if detections:
        if target_box:
            def target_score(item):
                candidate = {
                    "x": float(item.box.x), "y": float(item.box.y),
                    "width": float(item.box.width), "height": float(item.box.height),
                }
                overlap = _face_box_iou(candidate, target_box)
                tcx = float(target_box.get("x", 0)) + float(target_box.get("width", 0)) / 2
                tcy = float(target_box.get("y", 0)) + float(target_box.get("height", 0)) / 2
                ccx = candidate["x"] + candidate["width"] / 2
                ccy = candidate["y"] + candidate["height"] / 2
                scale = max(float(target_box.get("width", 1)), float(target_box.get("height", 1)), 1.0)
                centre_penalty = math.hypot(ccx - tcx, ccy - tcy) / scale
                return overlap * 10.0 - centre_penalty

            detection = max(detections, key=target_score)
        else:
            detection = max(detections, key=lambda item: item.box.width * item.box.height)
        crop = detection.crop(image, padding=0.16)
        confidence = float(detection.confidence)
        face_box = {
            "x": int(detection.box.x), "y": int(detection.box.y),
            "width": int(detection.box.width), "height": int(detection.box.height),
        }
    else:
        h, w = image.shape[:2]
        detection = Detection(kind="face", box=BoundingBox(x=0, y=0, width=w, height=h), confidence=0.5)
        crop = image
        confidence = 0.5
        face_box = {"x": 0, "y": 0, "width": w, "height": h}
    vector = system.embedding(image, detection)
    if vector is None:
        raise HTTPException(status_code=422, detail="No usable face embedding could be produced")
    return vector.astype(np.float32), confidence, crop, face_box


def _detect_people(frame: np.ndarray) -> list[dict[str, Any]]:
    """Local full-body detector for height support; empty is better than a fake box."""
    h, w = frame.shape[:2]
    scale = min(1.0, 640 / max(w, 1))
    small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
    try:
        boxes, weights = _hog_people_detector().detectMultiScale(
            small, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
    except cv2.error:
        return []
    out: list[dict[str, Any]] = []
    inv = 1 / scale
    for (x, y, bw, bh), weight in zip(boxes, weights):
        x, y, bw, bh = [int(round(v * inv)) for v in (x, y, bw, bh)]
        if bh < 60:
            continue
        out.append({
            "x": max(0, x), "y": max(0, y), "width": min(w - x, bw),
            "height": min(h - y, bh), "confidence": round(float(weight), 4),
        })
    return sorted(out, key=lambda item: item["width"] * item["height"], reverse=True)


def _appearance_from_box(frame: np.ndarray, box: dict[str, Any] | None) -> dict[str, Any]:
    if not box:
        return {
            "upper_colour": "Unknown", "lower_colour": "Unknown",
            "appearance_confidence": 0.0, "headwear": "Unknown", "carried_item": "Unknown",
        }
    try:
        from sentinel_camera_ai.colour import appearance_colours
        from sentinel_camera_ai.schemas import BoundingBox
        bbox = BoundingBox(
            x=max(0, int(box["x"])), y=max(0, int(box["y"])),
            width=max(1, int(box["width"])), height=max(1, int(box["height"])),
        )
        upper, lower, confidence = appearance_colours(frame, bbox)
        return {
            "upper_colour": upper,
            "lower_colour": lower,
            "appearance_confidence": confidence,
            "headwear": "Unknown",
            "carried_item": "Unknown",
        }
    except Exception:
        return {
            "upper_colour": "Unknown", "lower_colour": "Unknown",
            "appearance_confidence": 0.0, "headwear": "Unknown", "carried_item": "Unknown",
        }


@router.post("/api/member/face-detect")
async def detect_member_faces(image: UploadFile = File(...)):
    raw = await image.read()
    if not raw or len(raw) > MAX_FACE_UPLOAD:
        raise HTTPException(status_code=413, detail="camera frame is empty or too large")
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=415, detail="camera frame is not a valid image")
    system = _face_system()
    detections = system.detect(frame)
    people = _detect_people(frame)
    appearance = _appearance_from_box(frame, people[0] if people else None)
    return {
        "width": int(frame.shape[1]),
        "height": int(frame.shape[0]),
        "detector": system.detector_name,
        "faces": [
            {
                "x": int(item.box.x), "y": int(item.box.y),
                "width": int(item.box.width), "height": int(item.box.height),
                "confidence": round(float(item.confidence), 4),
            }
            for item in detections
        ],
        "persons": people[:3],
        "appearance": appearance,
    }


def _decode_embedding(blob: bytes | None, size: int | None) -> np.ndarray | None:
    if blob is None or not size:
        return None
    return np.frombuffer(blob, dtype=np.float32, count=size).copy()


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    cosine = float(np.dot(a, b) / denom)
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def _calibration_from_row(row: sqlite3.Row | dict[str, Any] | None) -> CameraCalibration | None:
    if not row:
        return None
    data = dict(row)
    return CameraCalibration(
        camera_id=data["camera_id"],
        image_width=int(data["image_width"]),
        image_height=int(data["image_height"]),
        mode=data["mode"],
        mount_height_m=data.get("mount_height_m"),
        tilt_deg=data.get("tilt_deg"),
        horizontal_fov_deg=data.get("horizontal_fov_deg"),
        horizon_y=data.get("horizon_y"),
        ref_height_m=data.get("ref_height_m"),
        ref_foot_y=data.get("ref_foot_y"),
        ref_head_y=data.get("ref_head_y"),
        calibration_score=float(data.get("calibration_score") or 100),
    )


def _estimate_height_for_sighting(
    db: sqlite3.Connection,
    camera_id: str,
    person_boxes: list[dict[str, Any]],
    image_width: int,
    image_height: int,
) -> dict[str, Any]:
    cal_row = db.execute(
        "SELECT * FROM member_camera_calibrations WHERE camera_id=?", (camera_id,)
    ).fetchone()
    if not cal_row:
        return {"height_status": "Camera not calibrated"}
    if not person_boxes:
        return {"height_status": "Full body not visible; height not estimated"}
    cal = _calibration_from_row(cal_row)
    assert cal is not None
    # Browser boxes are in the current frame dimensions. Scale to calibration size.
    sx = cal.image_width / max(image_width, 1)
    sy = cal.image_height / max(image_height, 1)
    scaled = [
        {
            "x": float(box.get("x", 0)) * sx,
            "y": float(box.get("y", 0)) * sy,
            "width": float(box.get("width", 0)) * sx,
            "height": float(box.get("height", 0)) * sy,
        }
        for box in person_boxes[-12:]
    ]
    observations = observations_from_person_boxes(scaled, cal.image_height, cal.image_width)
    try:
        estimate = estimate_height(cal, observations)
        return {**estimate.to_dict(), "height_status": "ESTIMATED"}
    except HeightUnavailable as exc:
        return {"height_status": str(exc)}


def _journey_metrics(previous: sqlite3.Row | None, camera: dict[str, Any], captured_at: str) -> dict[str, Any]:
    if not previous:
        return {
            "distance_m": 0.0, "speed_kmh": 0.0, "direction": "FIRST_SIGHTING",
            "plausible": True, "minutes": 0.0,
        }
    distance = _distance_metres(
        float(previous["latitude"]), float(previous["longitude"]),
        float(camera["latitude"]), float(camera["longitude"]),
    )
    old_dt, new_dt = _parse_dt(previous["captured_at"]), _parse_dt(captured_at)
    seconds = abs((new_dt - old_dt).total_seconds()) if old_dt and new_dt else 0.0
    speed = 0.0 if seconds <= 0.01 else (distance / 1000) / (seconds / 3600)
    return {
        "distance_m": round(distance, 1),
        "speed_kmh": round(speed, 1),
        "direction": _direction(
            float(previous["latitude"]), float(previous["longitude"]),
            float(camera["latitude"]), float(camera["longitude"]),
        ),
        "plausible": speed <= MAX_PERSON_SPEED_KMH or distance <= 20,
        "minutes": round(seconds / 60, 2),
    }


def _match_breakdown(
    similarity: float,
    previous: sqlite3.Row | None,
    height: dict[str, Any],
    appearance: dict[str, Any],
    journey: dict[str, Any],
) -> dict[str, Any]:
    reasons = [f"Face embedding similarity {similarity * 100:.0f}%"]
    components: dict[str, Any] = {"face_similarity": round(similarity * 100, 1)}
    scores: list[tuple[float, float]] = [(similarity * 100, 0.75)]

    previous_height = None
    current_height = None
    if previous and previous["height_low_m"] is not None and previous["height_high_m"] is not None:
        previous_height = (float(previous["height_low_m"]), float(previous["height_high_m"]))
    if height.get("height_low_m") is not None and height.get("height_high_m") is not None:
        current_height = (float(height["height_low_m"]), float(height["height_high_m"]))
    overlap = _height_overlap(previous_height, current_height)
    components["height_overlap"] = None if overlap is None else round(overlap * 100, 1)
    if overlap is not None:
        scores.append((overlap * 100, 0.10))
        reasons.append("Height bands are compatible" if overlap >= 0.35 else "Height bands do not support the match")
    else:
        reasons.append("Height unavailable on one or both sightings")

    known = 0
    agreed = 0
    for field, label in (("upper_colour", "upper clothing"), ("lower_colour", "lower clothing")):
        current = appearance.get(field)
        prior = previous[field] if previous else None
        if current and prior and current != "Unknown" and prior != "Unknown":
            known += 1
            if current == prior:
                agreed += 1
                reasons.append(f"{label.title()} agrees ({current})")
            else:
                reasons.append(f"{label.title()} differs ({prior} vs {current})")
    appearance_score = None if known == 0 else (agreed / known) * 100
    components["appearance_agreement"] = None if appearance_score is None else round(appearance_score, 1)
    if appearance_score is not None:
        scores.append((appearance_score, 0.10))
    else:
        reasons.append("Clothing comparison unavailable")

    journey_score = 100.0 if journey["plausible"] else 0.0
    components["journey_plausibility"] = journey_score
    scores.append((journey_score, 0.05))
    reasons.append(
        f"Journey plausible: {journey['distance_m']:.0f} m in {journey['minutes']:.1f} min"
        if journey["plausible"]
        else f"Journey implausible at {journey['speed_kmh']:.1f} km/h; human review required"
    )
    weight_total = sum(weight for _, weight in scores)
    overall = sum(score * weight for score, weight in scores) / max(weight_total, 1e-9)
    components["overall_evidence"] = round(overall, 1)
    return {
        "overall": round(overall, 1),
        "components": components,
        "reasons": reasons,
        "human_review_required": True,
    }


def _sighting_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["media_url"] = f"/api/member/face-media/{item['media_name']}" if item.get("media_name") else None
    item["person_box"] = _json_obj(item.get("person_box_json"), None)
    item["match_breakdown"] = _json_obj(item.get("match_breakdown_json"), None)
    # A face profile keeps its anonymous system label, while each household can
    # attach its own human-reviewed name and status.  The effective fields make
    # the recent-sightings UI reflect that household decision immediately.
    if "anonymous_label" in item:
        item["effective_label"] = item.get("viewer_display_label") or item.get("anonymous_label")
        item["effective_status"] = item.get("viewer_status") or "UNKNOWN"
    item.pop("embedding", None)
    item.pop("embedding_size", None)
    return item


def _incident_payload(db: sqlite3.Connection, incident_id: str) -> dict[str, Any] | None:
    _expire_incidents(db)
    incident = db.execute(
        """
        SELECT i.*, p.anonymous_label, u.display_name AS origin_display_name,
               c.household AS origin_household, c.suburb AS origin_suburb,
               c.latitude AS origin_latitude, c.longitude AS origin_longitude
        FROM member_incidents i
        JOIN face_profiles p ON p.profile_id=i.profile_id
        JOIN member_users u ON u.user_id=i.origin_user_id
        JOIN member_cameras c ON c.camera_id=i.origin_camera_id
        WHERE i.incident_id=?
        """,
        (incident_id,),
    ).fetchone()
    if not incident:
        return None
    notices = db.execute(
        """
        SELECT n.*, u.display_name, c.household, c.suburb, c.latitude, c.longitude
        FROM member_camera_notifications n
        JOIN member_users u ON u.user_id=n.user_id
        JOIN member_cameras c ON c.camera_id=n.camera_id
        WHERE n.incident_id=?
        """,
        (incident_id,),
    ).fetchall()
    payload = dict(incident)
    origin_lat = float(payload["origin_latitude"])
    origin_lon = float(payload["origin_longitude"])
    items = []
    for row in notices:
        item = dict(row)
        item["distance_from_origin_m"] = round(
            _distance_metres(origin_lat, origin_lon, float(item["latitude"]), float(item["longitude"])), 1
        )
        items.append(item)
    payload["notifications"] = sorted(items, key=lambda item: item["distance_from_origin_m"])
    return payload


def _active_incident_for_profile(db: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    _expire_incidents(db)
    row = db.execute(
        "SELECT incident_id FROM member_incidents WHERE profile_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (profile_id,),
    ).fetchone()
    return _incident_payload(db, row["incident_id"]) if row else None


def _start_incident_in_db(
    db: sqlite3.Connection,
    sighting: sqlite3.Row,
    incident_type: str,
    duration_minutes: int,
    notes: str,
    confirmed_by: str,
) -> tuple[bool, dict[str, Any]]:
    existing = db.execute(
        "SELECT incident_id FROM member_incidents WHERE profile_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (sighting["profile_id"],),
    ).fetchone()
    if existing:
        payload = _incident_payload(db, existing["incident_id"])
        assert payload is not None
        return False, payload

    camera = db.execute("SELECT * FROM member_cameras WHERE camera_id=?", (sighting["camera_id"],)).fetchone()
    incident_id = f"INC-{uuid.uuid4().hex[:10].upper()}"
    started = _now_dt()
    expires = started + timedelta(minutes=duration_minutes)
    db.execute(
        """
        INSERT INTO member_incidents(
            incident_id, profile_id, origin_user_id, origin_camera_id, origin_sighting_id,
            incident_type, status, started_at, updated_at, duration_minutes, expires_at,
            notes, confirmed_by
        ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)
        """,
        (
            incident_id, sighting["profile_id"], sighting["user_id"], sighting["camera_id"],
            sighting["sighting_id"], incident_type.strip().upper() or "TRESPASSING",
            started.isoformat(), started.isoformat(), duration_minutes, expires.isoformat(),
            notes.strip(), confirmed_by.strip() or "Member demo operator",
        ),
    )
    cameras = db.execute("SELECT * FROM member_cameras ORDER BY user_id, camera_id").fetchall()
    for item in cameras:
        origin = item["camera_id"] == sighting["camera_id"]
        status = "ORIGIN_CONFIRMED" if origin else "WATCH_ACTIVE"
        reason = (
            f"Confirmed {incident_type.replace('_', ' ').lower()} origin at {camera['household']}"
            if origin else f"Neighbour watch activated from {camera['household']}"
        )
        notification_id = f"NOTE-{uuid.uuid4().hex[:10].upper()}"
        db.execute(
            """
            INSERT INTO member_camera_notifications(
                notification_id, incident_id, camera_id, user_id, status, reason,
                created_at, updated_at, captured_sighting_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id, incident_id, item["camera_id"], item["user_id"], status,
                reason, started.isoformat(), started.isoformat(),
                sighting["sighting_id"] if origin else None,
            ),
        )
        _audit(
            db, "member_camera_notifications", notification_id, "INSERT", confirmed_by,
            f"{item['household']} camera status set to {status}",
            {"incident_id": incident_id, "camera_id": item["camera_id"], "status": status},
        )
        _create_member_alert(
            db,
            dedupe_key=f"INCIDENT-{incident_id}-{item['user_id']}",
            user_id=item["user_id"],
            incident_id=incident_id,
            profile_id=sighting["profile_id"],
            sighting_id=sighting["sighting_id"],
            alert_type="INCIDENT_CONFIRMED" if origin else "NEIGHBOUR_WATCH",
            title="Incident confirmed" if origin else "Neighbour alert",
            body=(
                f"Incident confirmed at {camera['household']}. Security response and camera watch are active."
                if origin else
                f"A confirmed incident at {camera['household']} has armed your household camera for {duration_minutes} minutes."
            ),
            urgent=True,
            channels=["IN-APP", "NEIGHBOURS", "SECURITY", "WHATSAPP READY"],
            context={"incident_id": incident_id, "profile_id": sighting["profile_id"], "camera_id": item["camera_id"]},
        )
    db.execute(
        "UPDATE face_profiles SET system_status='ACTIVE_INCIDENT', review_required=1, last_classified_at=? WHERE profile_id=?",
        (started.isoformat(), sighting["profile_id"]),
    )
    _audit(
        db, "member_incidents", incident_id, "INSERT", confirmed_by,
        f"{incident_type.replace('_', ' ').title()} watch started for {duration_minutes} minutes",
        {
            "incident_id": incident_id, "profile_id": sighting["profile_id"],
            "origin_camera_id": sighting["camera_id"], "expires_at": expires.isoformat(),
        },
    )
    try:
        from sentinel_ops.community_api import record_community_system_message
        record_community_system_message(
            db,
            message=(
                f"Confirmed incident at {camera['household']}. "
                f"Neighbouring cameras have been armed for {duration_minutes} minutes."
            ),
            urgent=True,
            source="MEMBER_INCIDENT",
            dedupe_key=f"INCIDENT-{incident_id}",
        )
    except Exception:
        pass
    payload = _incident_payload(db, incident_id)
    assert payload is not None
    return True, payload


def _profile_payload(db: sqlite3.Connection, profile_id: str, viewer_user_id: str | None = None) -> dict[str, Any] | None:
    profile = db.execute("SELECT * FROM face_profiles WHERE profile_id=?", (profile_id,)).fetchone()
    if not profile:
        return None
    rows = db.execute(
        """
        SELECT s.*, u.display_name, c.household, c.suburb, c.device_label
        FROM face_sightings s
        JOIN member_users u ON u.user_id=s.user_id
        JOIN member_cameras c ON c.camera_id=s.camera_id
        WHERE s.profile_id=? ORDER BY s.captured_at
        """,
        (profile_id,),
    ).fetchall()
    sightings = [_sighting_payload(row) for row in rows]
    labels = [dict(row) for row in db.execute(
        """
        SELECT l.*, u.display_name, u.household
        FROM member_profile_labels l JOIN member_users u ON u.user_id=l.user_id
        WHERE l.profile_id=? ORDER BY l.updated_at DESC
        """,
        (profile_id,),
    ).fetchall()]
    viewer = next((item for item in labels if item["user_id"] == viewer_user_id), None)
    active = _active_incident_for_profile(db, profile_id)

    heights = [
        (float(item["height_low_m"]), float(item["height_high_m"]), float(item["height_point_m"] or 0))
        for item in sightings
        if item.get("height_low_m") is not None and item.get("height_high_m") is not None
    ]
    height_summary = None
    if heights:
        height_summary = {
            "low_m": round(median([item[0] for item in heights]), 2),
            "high_m": round(median([item[1] for item in heights]), 2),
            "point_m": round(median([item[2] for item in heights]), 2),
            "samples": len(heights),
        }
    upper = Counter(item["upper_colour"] for item in sightings if item.get("upper_colour") not in (None, "Unknown"))
    lower = Counter(item["lower_colour"] for item in sightings if item.get("lower_colour") not in (None, "Unknown"))
    latest = sightings[-1] if sightings else None
    property_count = len({item["user_id"] for item in sightings})
    camera_count = len({item["camera_id"] for item in sightings})
    return {
        **{key: value for key, value in dict(profile).items() if key not in {"embedding", "embedding_size"}},
        "status": _profile_status(db, profile_id, viewer_user_id),
        "viewer_classification": viewer or {
            "profile_id": profile_id, "user_id": viewer_user_id, "status": "UNKNOWN",
            "display_label": None, "category": None, "valid_until": None, "notes": None,
        },
        "labels": labels,
        "sightings": sightings,
        "latest_sighting": latest,
        "height": height_summary,
        "appearance": {
            "upper_colour": upper.most_common(1)[0][0] if upper else "Unknown",
            "lower_colour": lower.most_common(1)[0][0] if lower else "Unknown",
            "headwear": "Unknown",
            "carried_item": "Unknown",
        },
        "property_count": property_count,
        "camera_count": camera_count,
        "active_incident": active,
    }


@router.post("/api/member/face-sightings")
async def create_face_sighting(
    image: UploadFile = File(...),
    user_id: str = Form(...),
    camera_id: str = Form(...),
    browser_confidence: float | None = Form(None),
    person_boxes_json: str | None = Form(None),
    target_face_box_json: str | None = Form(None),
    image_width: int | None = Form(None),
    image_height: int | None = Form(None),
):
    user = _get_user(user_id)
    camera = _get_camera(camera_id, user_id)
    raw = await image.read()
    if not raw or len(raw) > MAX_FACE_UPLOAD:
        raise HTTPException(status_code=413, detail="camera frame is empty or too large")
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=415, detail="camera frame is not a valid image")

    target_box = _json_obj(target_face_box_json, None)
    if not isinstance(target_box, dict):
        target_box = None
    vector, server_confidence, crop, face_box = _embedding_from_image(frame, target_box)
    parsed_boxes = _json_obj(person_boxes_json, [])
    if not isinstance(parsed_boxes, list):
        parsed_boxes = []
    if not parsed_boxes:
        parsed_boxes = _detect_people(frame)
    primary_person = max(parsed_boxes, key=lambda item: float(item.get("width", 0)) * float(item.get("height", 0))) if parsed_boxes else None
    appearance = _appearance_from_box(frame, primary_person)
    captured_at = _now()
    initialise_member_store()

    with connect() as db:
        height = _estimate_height_for_sighting(
            db, camera_id, parsed_boxes,
            int(image_width or frame.shape[1]), int(image_height or frame.shape[0]),
        )
        profiles = db.execute(
            "SELECT profile_id, anonymous_label, embedding, embedding_size, sighting_count, system_status FROM face_profiles"
        ).fetchall()
        best: dict[str, Any] | None = None
        for profile in profiles:
            candidate = _decode_embedding(profile["embedding"], profile["embedding_size"])
            if candidate is None:
                continue
            score = _similarity(vector, candidate)
            if best is None or score > best["similarity"]:
                best = {"row": profile, "similarity": score, "embedding": candidate}

        matched = bool(best and best["similarity"] >= FACE_MATCH_THRESHOLD)
        if matched:
            profile_id = best["row"]["profile_id"]
            anonymous_label = best["row"]["anonymous_label"]
            prior_count = int(best["row"]["sighting_count"])
            previous = db.execute(
                "SELECT * FROM face_sightings WHERE profile_id=? ORDER BY captured_at DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
            merged = (best["embedding"] * prior_count + vector) / (prior_count + 1)
            norm = float(np.linalg.norm(merged))
            if norm > 1e-12:
                merged = merged / norm
            similarity = float(best["similarity"])
        else:
            profile_id = f"FACE-{uuid.uuid4().hex[:8].upper()}"
            sequence = int(db.execute("SELECT COUNT(*) AS n FROM face_profiles").fetchone()["n"]) + 1
            anonymous_label = f"Anonymous visitor {sequence:02d}"
            previous = None
            similarity = 1.0
            merged = vector

        journey = _journey_metrics(previous, camera, captured_at)
        breakdown = _match_breakdown(similarity, previous, height, appearance, journey)
        review_required = int(matched and (not journey["plausible"] or breakdown["overall"] < 72))
        prior_system_status = str(best["row"]["system_status"] or "UNKNOWN") if matched and best else "UNKNOWN"
        # Never downgrade a human-confirmed intruder or an active incident merely
        # because another biometric sighting was captured. The old implementation
        # replaced these states with REPEAT_VISITOR on every match, which could
        # silently break downstream neighbour and security alerts.
        system_status = (
            prior_system_status
            if prior_system_status in {"CONFIRMED_INTRUDER", "ACTIVE_INCIDENT"}
            else ("REPEAT_VISITOR" if matched else "UNKNOWN")
        )
        if matched:
            db.execute(
                """
                UPDATE face_profiles SET embedding=?, embedding_size=?, last_seen=?,
                    sighting_count=sighting_count+1, system_status=?, review_required=?
                WHERE profile_id=?
                """,
                (
                    merged.astype(np.float32).tobytes(), merged.size, captured_at,
                    system_status, review_required, profile_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO face_profiles(
                    profile_id, anonymous_label, embedding, embedding_size, first_seen,
                    last_seen, sighting_count, system_status, review_required
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'UNKNOWN', 0)
                """,
                (profile_id, anonymous_label, vector.tobytes(), vector.size, captured_at, captured_at),
            )
            _audit(
                db, "face_profiles", profile_id, "INSERT", user_id,
                f"Created {anonymous_label}", {"profile_id": profile_id, "anonymous_label": anonymous_label},
            )

        sighting_id = f"SIGHT-{uuid.uuid4().hex[:10].upper()}"
        media_name = f"{sighting_id}.jpg"
        cv2.imwrite(str(FACE_MEDIA_ROOT / media_name), crop)
        confidence = max(float(server_confidence), float(browser_confidence or 0.0))
        db.execute(
            """
            INSERT INTO face_sightings(
                sighting_id, profile_id, user_id, camera_id, captured_at, similarity,
                detection_confidence, media_name, latitude, longitude, embedding, embedding_size,
                height_low_m, height_high_m, height_point_m, height_quality, height_method,
                height_status, upper_colour, lower_colour, appearance_confidence, headwear,
                carried_item, person_box_json, match_breakdown_json, journey_distance_m,
                journey_speed_kmh, journey_direction, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sighting_id, profile_id, user_id, camera_id, captured_at, similarity,
                confidence, media_name, camera["latitude"], camera["longitude"],
                vector.tobytes(), vector.size,
                height.get("height_low_m"), height.get("height_high_m"), height.get("height_point_m"),
                height.get("quality"), height.get("method"), height.get("height_status"),
                appearance["upper_colour"], appearance["lower_colour"], appearance["appearance_confidence"],
                appearance["headwear"], appearance["carried_item"],
                _safe_json(primary_person) if primary_person else None, _safe_json(breakdown),
                journey["distance_m"], journey["speed_kmh"], journey["direction"],
                "REVIEW_REQUIRED" if review_required else "UNREVIEWED",
            ),
        )
        db.execute("UPDATE member_cameras SET last_seen_at=? WHERE camera_id=?", (captured_at, camera_id))
        _audit(
            db, "face_sightings", sighting_id, "INSERT", user_id,
            f"{anonymous_label} captured at {camera['household']}",
            {
                "sighting_id": sighting_id, "profile_id": profile_id, "camera_id": camera_id,
                "similarity": round(similarity, 3), "height": height,
                "appearance": appearance, "journey": journey,
            },
        )

        _expire_incidents(db)
        active = db.execute(
            "SELECT incident_id FROM member_incidents WHERE profile_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        incident_watch = None
        auto_incident_started = False
        # Resolve the operational status from both the profile and human review
        # labels. This keeps automatic watch reactivation reliable even after an
        # earlier incident has expired or a local household label was the source of
        # the confirmed-intruder decision.
        profile_system_status = _profile_status(db, profile_id)
        if not active and matched and profile_system_status in {"CONFIRMED_INTRUDER", "ACTIVE_INCIDENT"}:
            fresh_sighting = db.execute(
                "SELECT * FROM face_sightings WHERE sighting_id=?",
                (sighting_id,),
            ).fetchone()
            if fresh_sighting:
                auto_incident_started, incident_watch = _start_incident_in_db(
                    db,
                    fresh_sighting,
                    "TRESPASSING",
                    30,
                    "Automatically reactivated after a repeat sighting of a human-confirmed intruder profile.",
                    "MzansiMesh automatic watch",
                )
                if incident_watch:
                    active = {"incident_id": incident_watch["incident_id"]}

        if active:
            incident_id = active["incident_id"]
            db.execute("UPDATE member_incidents SET updated_at=? WHERE incident_id=?", (captured_at, incident_id))
            notification_id = f"NOTE-{uuid.uuid4().hex[:10].upper()}"
            match_reason = f"Confirmed intruder profile captured at {camera['household']}"
            db.execute(
                """
                INSERT INTO member_camera_notifications(
                    notification_id, incident_id, camera_id, user_id, status, reason,
                    created_at, updated_at, captured_sighting_id
                ) VALUES (?, ?, ?, ?, 'MATCH_CAPTURED', ?, ?, ?, ?)
                ON CONFLICT(incident_id, camera_id) DO UPDATE SET
                    status='MATCH_CAPTURED', reason=excluded.reason,
                    updated_at=excluded.updated_at, captured_sighting_id=excluded.captured_sighting_id
                """,
                (
                    notification_id, incident_id, camera_id, user_id,
                    match_reason,
                    captured_at, captured_at, sighting_id,
                ),
            )
            # Re-alert every other participating household. Their camera remains in
            # watch mode, but the reason and timestamp change so the UI creates a
            # fresh notification instead of silently retaining the original flag.
            neighbour_reason = (
                f"Repeat intruder seen at {camera['household']}. "
                "Keep this camera on active watch and review any match immediately."
            )
            db.execute(
                """
                UPDATE member_camera_notifications
                SET status='WATCH_ACTIVE', reason=?, updated_at=?, captured_sighting_id=NULL
                WHERE incident_id=? AND camera_id<>?
                """,
                (neighbour_reason, captured_at, incident_id, camera_id),
            )
            participating_users = db.execute(
                "SELECT DISTINCT user_id, household FROM member_cameras ORDER BY user_id"
            ).fetchall()
            for participant in participating_users:
                _create_member_alert(
                    db,
                    dedupe_key=f"REPEAT-{sighting_id}-{participant['user_id']}",
                    user_id=participant["user_id"],
                    incident_id=incident_id,
                    profile_id=profile_id,
                    sighting_id=sighting_id,
                    alert_type="REPEAT_INTRUDER_MATCH",
                    title=f"Repeat intruder detected at {camera['household']}",
                    body=(
                        f"A human-confirmed intruder profile was detected at {camera['household']}. "
                        "Neighbouring cameras remain armed and the security control room has been alerted."
                    ),
                    urgent=True,
                    channels=["IN-APP", "NEIGHBOURS", "SECURITY", "WHATSAPP READY"],
                    context={
                        "incident_id": incident_id,
                        "profile_id": profile_id,
                        "sighting_id": sighting_id,
                        "camera_id": camera_id,
                        "detected_household": camera["household"],
                    },
                )
            _audit(
                db, "member_camera_notifications", notification_id, "UPSERT", "MzansiMesh matcher",
                f"Active-watch match captured at {camera['household']} and neighbouring households re-alerted",
                {"incident_id": incident_id, "sighting_id": sighting_id, "camera_id": camera_id},
            )
            try:
                from sentinel_ops.community_api import record_community_system_message
                record_community_system_message(
                    db,
                    message=(
                        f"Active watch match at {camera['household']}. "
                        "Neighbouring households and the security control room have been alerted."
                    ),
                    urgent=True,
                    source="CAMERA_MATCH",
                    dedupe_key=f"MATCH-{sighting_id}",
                )
            except Exception:
                pass
            incident_watch = _incident_payload(db, incident_id)

        rows = db.execute(
            """
            SELECT s.*, u.display_name, c.household, c.suburb, c.device_label
            FROM face_sightings s
            JOIN member_users u ON u.user_id=s.user_id
            JOIN member_cameras c ON c.camera_id=s.camera_id
            WHERE s.profile_id=? ORDER BY s.captured_at
            """,
            (profile_id,),
        ).fetchall()
        viewer_classification = db.execute(
            "SELECT * FROM member_profile_labels WHERE profile_id=? AND user_id=?",
            (profile_id, user_id),
        ).fetchone()

    security_dispatch = None
    if incident_watch:
        try:
            from sentinel_ops.security_dispatch import (
                create_dispatch_for_member_incident,
                initialise_security_store,
                queue_repeat_intruder_notifications,
            )
            initialise_security_store()
            with connect() as security_db:
                security_dispatch = create_dispatch_for_member_incident(
                    security_db,
                    incident_watch["incident_id"],
                    actor="MzansiMesh repeat match" if not auto_incident_started else "MzansiMesh automatic watch",
                )
                if matched:
                    security_dispatch = queue_repeat_intruder_notifications(
                        security_db,
                        incident_watch["incident_id"],
                        household=camera["household"],
                        sighting_id=sighting_id,
                        actor="MzansiMesh repeat match",
                    )
        except Exception as exc:
            security_dispatch = {"status": "DISPATCH_BRIDGE_ERROR", "detail": str(exc)}

    sightings = [_sighting_payload(row) for row in rows]
    other_users = sorted({row["display_name"] for row in sightings if row["user_id"] != user_id})
    return {
        "sighting_id": sighting_id,
        "profile_id": profile_id,
        "anonymous_label": anonymous_label,
        "classification": "REPEAT_VISITOR_CANDIDATE" if matched else "NEW_VISITOR",
        "profile_status": "ACTIVE_INCIDENT" if incident_watch else ("REVIEW_REQUIRED" if review_required else system_status),
        "viewer_classification": dict(viewer_classification) if viewer_classification else {"status": "UNKNOWN"},
        "matched": matched,
        "similarity": round(similarity, 3),
        "threshold": FACE_MATCH_THRESHOLD,
        "sighting_count": len(sightings),
        "seen_at_other_properties": other_users,
        "current_user": user["display_name"],
        "camera": camera,
        "sightings": sightings,
        "height": height,
        "appearance": appearance,
        "journey": journey,
        "match_breakdown": breakdown,
        "face_box": face_box,
        "incident_watch": incident_watch,
        "auto_incident_started": auto_incident_started,
        "security_dispatch": security_dispatch,
        "notice": (
            "A human-confirmed intruder profile triggered an automatic neighbourhood watch."
            if auto_incident_started else
            "Anonymous biometric candidate only; a person must review and classify it before action."
        ),
    }


@router.get("/api/member/{user_id}/face-sightings")
def list_face_sightings(user_id: str, limit: int = Query(30, ge=1, le=200)):
    _get_user(user_id)
    initialise_member_store()
    with connect() as db:
        rows = db.execute(
            """
            SELECT s.*, p.anonymous_label, p.sighting_count, u.display_name,
                   c.household, c.suburb, c.device_label,
                   l.status AS viewer_status,
                   l.display_label AS viewer_display_label,
                   l.category AS viewer_category
            FROM face_sightings s
            JOIN face_profiles p ON p.profile_id=s.profile_id
            JOIN member_users u ON u.user_id=s.user_id
            JOIN member_cameras c ON c.camera_id=s.camera_id
            LEFT JOIN member_profile_labels l
              ON l.profile_id=s.profile_id AND l.user_id=?
            WHERE s.user_id=? OR s.profile_id IN (
                SELECT profile_id FROM face_sightings WHERE user_id=?
            )
            ORDER BY s.captured_at DESC LIMIT ?
            """,
            (user_id, user_id, user_id, limit),
        ).fetchall()
    return {"user_id": user_id, "count": len(rows), "sightings": [_sighting_payload(row) for row in rows]}


@router.get("/api/member/visitors")
def list_visitors(user_id: str = Query(...), limit: int = Query(50, ge=1, le=200)):
    _get_user(user_id)
    initialise_member_store()
    with connect() as db:
        _expire_incidents(db)
        profile_ids = [row["profile_id"] for row in db.execute(
            """
            SELECT DISTINCT p.profile_id, p.last_seen
            FROM face_profiles p
            JOIN face_sightings s ON s.profile_id=p.profile_id
            WHERE s.user_id=? OR p.profile_id IN (
                SELECT profile_id FROM member_incidents WHERE status='ACTIVE'
            )
            ORDER BY p.last_seen DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()]
        profiles = [_profile_payload(db, profile_id, user_id) for profile_id in profile_ids]
    return {"user_id": user_id, "count": len(profiles), "visitors": [item for item in profiles if item]}


@router.get("/api/member/visitors/{profile_id}")
def get_visitor(profile_id: str, user_id: str = Query(...)):
    _get_user(user_id)
    initialise_member_store()
    with connect() as db:
        payload = _profile_payload(db, profile_id, user_id)
    if not payload:
        raise HTTPException(status_code=404, detail="visitor profile not found")
    return payload


@router.post("/api/member/visitors/{profile_id}/classify")
def classify_visitor(profile_id: str, body: ProfileClassificationIn):
    user = _get_user(body.user_id)
    if body.status not in ALLOWED_PROFILE_STATUSES:
        raise HTTPException(status_code=422, detail="unsupported visitor status")
    if body.status == "TRUSTED" and not (body.display_label or "").strip():
        raise HTTPException(status_code=422, detail="A trusted visitor needs a household label")
    if body.status == "CONFIRMED_INTRUDER" and len(body.notes.strip()) < 3:
        raise HTTPException(status_code=422, detail="Confirmed intruder review requires a reason")
    initialise_member_store()
    with connect() as db:
        profile = db.execute("SELECT * FROM face_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not profile:
            raise HTTPException(status_code=404, detail="visitor profile not found")
        now = _now()
        db.execute(
            """
            INSERT INTO member_profile_labels(
                profile_id, user_id, status, display_label, category, valid_until,
                notes, updated_at, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, user_id) DO UPDATE SET
                status=excluded.status, display_label=excluded.display_label,
                category=excluded.category, valid_until=excluded.valid_until,
                notes=excluded.notes, updated_at=excluded.updated_at, updated_by=excluded.updated_by
            """,
            (
                profile_id, body.user_id, body.status,
                (body.display_label or "").strip() or None,
                (body.category or "").strip() or None,
                body.valid_until, body.notes.strip(), now, body.updated_by,
            ),
        )
        profile_system = {
            "CONFIRMED_INTRUDER": "CONFIRMED_INTRUDER",
            "REVIEW_REQUIRED": "REVIEW_REQUIRED",
            "CLEARED": "CLEARED",
        }.get(body.status, profile["system_status"])
        db.execute(
            "UPDATE face_profiles SET system_status=?, review_required=?, last_classified_at=? WHERE profile_id=?",
            (profile_system, int(body.status == "REVIEW_REQUIRED"), now, profile_id),
        )
        _audit(
            db, "member_profile_labels", f"{profile_id}:{body.user_id}", "UPSERT", body.updated_by,
            f"{profile['anonymous_label']} marked {body.status.replace('_', ' ').lower()} at {user['household']}",
            body.model_dump(),
        )
        incident = None
        if body.status == "CONFIRMED_INTRUDER" and body.start_incident_watch:
            sighting = None
            if body.sighting_id:
                sighting = db.execute(
                    "SELECT * FROM face_sightings WHERE sighting_id=? AND profile_id=?",
                    (body.sighting_id, profile_id),
                ).fetchone()
            if not sighting:
                sighting = db.execute(
                    "SELECT * FROM face_sightings WHERE profile_id=? AND user_id=? ORDER BY captured_at DESC LIMIT 1",
                    (profile_id, body.user_id),
                ).fetchone()
            if not sighting:
                raise HTTPException(status_code=422, detail="No sighting is available to start the incident watch")
            _, incident = _start_incident_in_db(
                db, sighting, body.incident_type, body.duration_minutes, body.notes, body.updated_by
            )
        payload = _profile_payload(db, profile_id, body.user_id)
        record_id = f"{profile_id}:{body.user_id}"
        pending_aws = db.execute(
            "SELECT COUNT(*) AS n FROM aws_sync_outbox WHERE status='LOCAL_PENDING'"
        ).fetchone()["n"]
    security_dispatch = None
    if incident:
        try:
            from sentinel_ops.security_dispatch import (
                create_dispatch_for_member_incident,
                initialise_security_store,
            )
            initialise_security_store()
            with connect() as security_db:
                security_dispatch = create_dispatch_for_member_incident(
                    security_db, incident["incident_id"], actor=body.updated_by
                )
        except Exception as exc:
            security_dispatch = {"status": "DISPATCH_BRIDGE_ERROR", "detail": str(exc)}
    return {
        "profile": payload,
        "incident": incident,
        "security_dispatch": security_dispatch,
        "database_write": {
            "engine": "SQLite",
            "table": "member_profile_labels",
            "record_id": record_id,
            "action": "UPSERT",
            "status": body.status,
            "committed": True,
        },
        "aws_outbox_pending": int(pending_aws),
    }


@router.post("/api/member/sightings/{sighting_id}/false-match")
def dismiss_false_match(sighting_id: str, body: FalseMatchIn):
    """Split one reviewed sighting out of an incorrectly linked face profile."""
    initialise_member_store()
    with connect() as db:
        sighting = db.execute("SELECT * FROM face_sightings WHERE sighting_id=?", (sighting_id,)).fetchone()
        if not sighting:
            raise HTTPException(status_code=404, detail="sighting not found")
        old_profile_id = sighting["profile_id"]
        count = db.execute(
            "SELECT COUNT(*) AS n FROM face_sightings WHERE profile_id=?", (old_profile_id,)
        ).fetchone()["n"]
        if count <= 1:
            db.execute("UPDATE face_sightings SET review_status='CLEARED' WHERE sighting_id=?", (sighting_id,))
            _audit(
                db, "face_sightings", sighting_id, "REVIEW", body.reviewer,
                "Single sighting cleared after review", {"reason": body.reason},
            )
            return {"split": False, "message": "Single sighting cleared; there was no repeat link to split."}
        vector = _decode_embedding(sighting["embedding"], sighting["embedding_size"])
        if vector is None:
            raise HTTPException(status_code=409, detail="This older sighting has no stored embedding and cannot be split automatically")
        new_profile_id = f"FACE-{uuid.uuid4().hex[:8].upper()}"
        sequence = int(db.execute("SELECT COUNT(*) AS n FROM face_profiles").fetchone()["n"]) + 1
        label = f"Anonymous visitor {sequence:02d}"
        db.execute(
            """
            INSERT INTO face_profiles(
                profile_id, anonymous_label, embedding, embedding_size, first_seen,
                last_seen, sighting_count, system_status, review_required, last_classified_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'CLEARED', 0, ?)
            """,
            (
                new_profile_id, label, vector.tobytes(), vector.size,
                sighting["captured_at"], sighting["captured_at"], _now(),
            ),
        )
        db.execute(
            "UPDATE face_sightings SET profile_id=?, review_status='FALSE_MATCH_SPLIT', similarity=1.0 WHERE sighting_id=?",
            (new_profile_id, sighting_id),
        )
        remaining = db.execute(
            "SELECT embedding, embedding_size, captured_at FROM face_sightings WHERE profile_id=? ORDER BY captured_at",
            (old_profile_id,),
        ).fetchall()
        vectors = [_decode_embedding(row["embedding"], row["embedding_size"]) for row in remaining]
        vectors = [item for item in vectors if item is not None]
        if vectors:
            average = np.mean(vectors, axis=0)
            norm = float(np.linalg.norm(average))
            if norm > 1e-12:
                average = average / norm
            db.execute(
                """
                UPDATE face_profiles SET embedding=?, embedding_size=?, first_seen=?, last_seen=?,
                    sighting_count=?, system_status=CASE WHEN ? >= 2 THEN 'REPEAT_VISITOR' ELSE 'UNKNOWN' END
                WHERE profile_id=?
                """,
                (
                    average.astype(np.float32).tobytes(), average.size,
                    remaining[0]["captured_at"], remaining[-1]["captured_at"],
                    len(remaining), len(remaining), old_profile_id,
                ),
            )
        _audit(
            db, "face_sightings", sighting_id, "FALSE_MATCH_SPLIT", body.reviewer,
            f"Sighting split from {old_profile_id} into {new_profile_id}",
            {"reason": body.reason, "old_profile_id": old_profile_id, "new_profile_id": new_profile_id},
        )
        old_payload = _profile_payload(db, old_profile_id, sighting["user_id"])
        new_payload = _profile_payload(db, new_profile_id, sighting["user_id"])
    return {"split": True, "old_profile": old_payload, "new_profile": new_payload}


@router.put("/api/member/cameras/{camera_id}/calibration")
def save_member_calibration(camera_id: str, body: CalibrationIn):
    camera = _get_camera(camera_id)
    data = body.model_dump()
    data["camera_id"] = camera_id
    data.pop("updated_by")
    cal = CameraCalibration(**data)
    try:
        cal.validate()
    except HeightUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    initialise_member_store()
    with connect() as db:
        now = _now()
        db.execute(
            """
            INSERT INTO member_camera_calibrations(
                camera_id, mode, image_width, image_height, mount_height_m, tilt_deg,
                horizontal_fov_deg, horizon_y, ref_height_m, ref_foot_y, ref_head_y,
                calibration_score, updated_at, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_id) DO UPDATE SET
                mode=excluded.mode, image_width=excluded.image_width,
                image_height=excluded.image_height, mount_height_m=excluded.mount_height_m,
                tilt_deg=excluded.tilt_deg, horizontal_fov_deg=excluded.horizontal_fov_deg,
                horizon_y=excluded.horizon_y, ref_height_m=excluded.ref_height_m,
                ref_foot_y=excluded.ref_foot_y, ref_head_y=excluded.ref_head_y,
                calibration_score=excluded.calibration_score, updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            """,
            (
                camera_id, body.mode, body.image_width, body.image_height,
                body.mount_height_m, body.tilt_deg, body.horizontal_fov_deg,
                body.horizon_y, body.ref_height_m, body.ref_foot_y, body.ref_head_y,
                body.calibration_score, now, body.updated_by,
            ),
        )
        payload = body.model_dump() | {"camera_id": camera_id, "household": camera["household"]}
        _audit(
            db, "member_camera_calibrations", camera_id, "UPSERT", body.updated_by,
            f"Height calibration saved for {camera['household']}", payload,
        )
    return {"camera_id": camera_id, "status": "CALIBRATED", "calibration": payload}


@router.get("/api/member/cameras/{camera_id}/calibration")
def get_member_calibration(camera_id: str):
    _get_camera(camera_id)
    initialise_member_store()
    with connect() as db:
        row = db.execute("SELECT * FROM member_camera_calibrations WHERE camera_id=?", (camera_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="camera has no stored height calibration")
    return dict(row)


@router.post("/api/member/incidents/start")
def start_member_incident_watch(body: IncidentWatchStart):
    if not body.confirmed_by_operator:
        raise HTTPException(status_code=422, detail="Operator confirmation is required")
    initialise_member_store()
    with connect() as db:
        sighting = db.execute(
            "SELECT s.* FROM face_sightings s WHERE s.sighting_id=?", (body.sighting_id,)
        ).fetchone()
        if not sighting:
            raise HTTPException(status_code=404, detail="sighting not found")
        created, incident = _start_incident_in_db(
            db, sighting, body.incident_type, body.duration_minutes, body.notes, body.confirmed_by
        )
    security_dispatch = None
    try:
        from sentinel_ops.security_dispatch import (
            create_dispatch_for_member_incident,
            initialise_security_store,
        )
        initialise_security_store()
        with connect() as security_db:
            security_dispatch = create_dispatch_for_member_incident(
                security_db, incident["incident_id"], actor=body.confirmed_by
            )
    except Exception as exc:
        security_dispatch = {"status": "DISPATCH_BRIDGE_ERROR", "detail": str(exc)}
    return {"created": created, "incident": incident, "security_dispatch": security_dispatch}


@router.post("/api/member/incidents/{incident_id}/close")
def close_member_incident(incident_id: str, body: IncidentCloseIn):
    initialise_member_store()
    with connect() as db:
        row = db.execute("SELECT * FROM member_incidents WHERE incident_id=?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="incident not found")
        now = _now()
        db.execute(
            "UPDATE member_incidents SET status='CLOSED', updated_at=?, ended_at=?, outcome=?, notes=CASE WHEN ?='' THEN notes ELSE ? END WHERE incident_id=?",
            (now, now, body.outcome, body.notes.strip(), body.notes.strip(), incident_id),
        )
        db.execute(
            "UPDATE member_camera_notifications SET status=CASE WHEN status='MATCH_CAPTURED' THEN status ELSE 'WATCH_CLOSED' END, updated_at=? WHERE incident_id=?",
            (now, incident_id),
        )
        _audit(
            db, "member_incidents", incident_id, "UPDATE", body.closed_by,
            f"Incident closed: {body.outcome}", body.model_dump(),
        )
        payload = _incident_payload(db, incident_id)
    security_dispatch = None
    try:
        from sentinel_ops.security_dispatch import (
            close_dispatch_for_member_incident,
            initialise_security_store,
        )
        initialise_security_store()
        with connect() as security_db:
            security_dispatch = close_dispatch_for_member_incident(
                security_db, incident_id, actor=body.closed_by
            )
    except Exception as exc:
        security_dispatch = {"status": "DISPATCH_BRIDGE_ERROR", "detail": str(exc)}
    return payload | {"security_dispatch": security_dispatch}


@router.get("/api/member/incidents/active")
def active_member_incidents():
    initialise_member_store()
    with connect() as db:
        _expire_incidents(db)
        rows = db.execute(
            "SELECT incident_id FROM member_incidents WHERE status='ACTIVE' ORDER BY started_at DESC"
        ).fetchall()
        incidents = [_incident_payload(db, row["incident_id"]) for row in rows]
    return {"count": len(incidents), "incidents": incidents}


@router.get("/api/member/incidents/{incident_id}/report")
def incident_report(incident_id: str):
    initialise_member_store()
    with connect() as db:
        incident = _incident_payload(db, incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="incident not found")
        profile = _profile_payload(db, incident["profile_id"], incident["origin_user_id"])
    return {
        "report_version": "sentinel-member-incident-v1",
        "generated_at": _now(),
        "incident": incident,
        "anonymous_profile": profile,
        "disclaimer": "Candidate biometric and appearance evidence for human review; not proof of identity or guilt.",
    }


@router.get("/api/member/alerts")
def member_alerts(
    user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Persistent priority alerts for household inboxes.

    Unknown visitors never enter this feed. Only confirmed incidents, neighbour
    watches and repeat intruder matches are stored here.
    """
    initialise_member_store()
    query = "SELECT * FROM member_alert_events"
    args: list[Any] = []
    if user_id:
        query += " WHERE user_id=?"
        args.append(user_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as db:
        rows = db.execute(query, tuple(args)).fetchall()
    alerts = []
    for row in rows:
        item = dict(row)
        item["urgent"] = bool(item.get("urgent"))
        item["channels"] = _json_obj(item.pop("channels_json", None), [])
        item["context"] = _json_obj(item.pop("context_json", None), {})
        alerts.append(item)
    return {"user_id": user_id, "count": len(alerts), "alerts": alerts}


@router.get("/api/notifications/priority")
def priority_notifications(
    scope: Literal["member", "security"] = Query(default="member"),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=80, ge=1, le=200),
):
    """Unified priority feed used by the bottom notification centre.

    Member scope returns the selected household's persistent incident alerts plus
    any security response messages tied to those incidents. Security scope returns
    control-room notifications only. Unknown visitors are deliberately excluded.
    """
    initialise_member_store()
    try:
        from sentinel_ops.security_dispatch import initialise_security_store
        initialise_security_store()
    except Exception:
        pass

    items: list[dict[str, Any]] = []
    with connect() as db:
        incident_ids: list[str] = []
        if scope == "member":
            member_query = "SELECT * FROM member_alert_events"
            member_args: list[Any] = []
            if user_id:
                member_query += " WHERE user_id=?"
                member_args.append(user_id)
            member_query += " ORDER BY created_at DESC LIMIT ?"
            member_args.append(limit)
            member_rows = db.execute(member_query, tuple(member_args)).fetchall()
            for row in member_rows:
                data = dict(row)
                incident_id = data.get("incident_id")
                if incident_id:
                    incident_ids.append(str(incident_id))
                items.append({
                    "event_id": f"MEMBER:{data['alert_id']}",
                    "source": "MEMBER",
                    "target_user_id": data.get("user_id"),
                    "title": data.get("title") or "MzansiMesh alert",
                    "body": data.get("body") or "",
                    "created_at": data.get("created_at"),
                    "urgent": bool(data.get("urgent")),
                    "channels": _json_obj(data.get("channels_json"), ["IN-APP"]),
                    "action": "member-trail",
                    "action_label": "Open incident trail",
                    "context": _json_obj(data.get("context_json"), {}),
                })

        table_names = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if scope == "security" and {"security_notifications", "security_dispatches"}.issubset(table_names):
            sec_rows = db.execute(
                """
                SELECT n.*, d.member_incident_id, d.incident_type, d.address,
                       d.selected_unit_id, d.eta_minutes
                FROM security_notifications n
                JOIN security_dispatches d ON d.dispatch_id=n.dispatch_id
                ORDER BY n.created_at DESC LIMIT ?
                """,
                (limit * 4,),
            ).fetchall()
            seen_control_room_events: set[tuple[str, str]] = set()
            for row in sec_rows:
                data = dict(row)
                message = str(data.get("message") or "Security response notification")
                alert_group = "REPEAT" if "REPEAT INTRUDER" in message.upper() else "INITIAL"
                dedupe = (str(data.get("dispatch_id") or ""), alert_group)
                if dedupe in seen_control_room_events:
                    continue
                seen_control_room_events.add(dedupe)
                lines = [line.strip() for line in message.splitlines() if line.strip()]
                title = lines[0] if lines else "Security response notification"
                body = " ".join(lines[1:]) if len(lines) > 1 else message
                items.append({
                    "event_id": f"SECURITY:{data['notification_id']}",
                    "source": "SECURITY",
                    "title": title,
                    "body": body,
                    "created_at": data.get("created_at"),
                    "urgent": "REPEAT INTRUDER" in message.upper() or data.get("status") == "QUEUED_LOCAL",
                    "channels": [data.get("channel") or "CONTROL ROOM", "IN-APP"],
                    "action": "security-response",
                    "action_label": "Open security response",
                    "context": {
                        "dispatch_id": data.get("dispatch_id"),
                        "unit_id": data.get("selected_unit_id") or data.get("unit_id"),
                        "incident_id": data.get("member_incident_id"),
                    },
                })

    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "scope": scope,
        "user_id": user_id,
        "count": min(len(items), limit),
        "items": items[:limit],
        "server_time": _now(),
    }


@router.post("/api/member/alerts/{alert_id}/read")
def mark_member_alert_read(alert_id: str):
    initialise_member_store()
    with connect() as db:
        row = db.execute("SELECT alert_id FROM member_alert_events WHERE alert_id=?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="alert not found")
        db.execute("UPDATE member_alert_events SET read_at=? WHERE alert_id=?", (_now(), alert_id))
    return {"alert_id": alert_id, "read": True}


@router.get("/api/member/mesh-state")
def member_mesh_state():
    initialise_member_store()
    with connect() as db:
        _expire_incidents(db)
        cameras = [dict(row) for row in db.execute(
            """
            SELECT c.*, u.display_name,
                   CASE WHEN cal.camera_id IS NULL THEN 0 ELSE 1 END AS calibrated
            FROM member_cameras c
            JOIN member_users u ON u.user_id=c.user_id
            LEFT JOIN member_camera_calibrations cal ON cal.camera_id=c.camera_id
            ORDER BY c.user_id, c.camera_id
            """
        ).fetchall()]
        profile_rows = db.execute("SELECT * FROM face_profiles ORDER BY last_seen DESC").fetchall()
        trails = []
        for profile in profile_rows:
            points = [
                _sighting_payload(row) for row in db.execute(
                    """
                    SELECT s.*, u.display_name, c.household, c.suburb, c.device_label
                    FROM face_sightings s
                    JOIN member_users u ON u.user_id=s.user_id
                    JOIN member_cameras c ON c.camera_id=s.camera_id
                    WHERE s.profile_id=? ORDER BY s.captured_at
                    """,
                    (profile["profile_id"],),
                ).fetchall()
            ]
            trails.append({
                "profile_id": profile["profile_id"],
                "anonymous_label": profile["anonymous_label"],
                "status": _profile_status(db, profile["profile_id"]),
                "sighting_count": profile["sighting_count"],
                "first_seen": profile["first_seen"],
                "last_seen": profile["last_seen"],
                "points": points,
            })
        incident_rows = db.execute(
            "SELECT incident_id FROM member_incidents WHERE status='ACTIVE' ORDER BY started_at DESC"
        ).fetchall()
        incidents = [_incident_payload(db, row["incident_id"]) for row in incident_rows]
    return {
        "camera_count": len(cameras),
        "cameras": cameras,
        "trail_count": len(trails),
        "trails": trails,
        "active_incidents": incidents,
        "storage": "SQLite local demo database + AWS outbox",
    }


@router.get("/api/member/face-trails")
def face_trails():
    initialise_member_store()
    with connect() as db:
        profiles = db.execute(
            "SELECT * FROM face_profiles WHERE sighting_count>=2 ORDER BY last_seen DESC"
        ).fetchall()
        trails = []
        for profile in profiles:
            rows = db.execute(
                """
                SELECT s.*, u.display_name, c.household, c.suburb, c.device_label
                FROM face_sightings s
                JOIN member_users u ON u.user_id=s.user_id
                JOIN member_cameras c ON c.camera_id=s.camera_id
                WHERE s.profile_id=? ORDER BY s.captured_at
                """,
                (profile["profile_id"],),
            ).fetchall()
            trails.append({
                "profile_id": profile["profile_id"],
                "anonymous_label": profile["anonymous_label"],
                "status": _profile_status(db, profile["profile_id"]),
                "sighting_count": profile["sighting_count"],
                "first_seen": profile["first_seen"],
                "last_seen": profile["last_seen"],
                "points": [_sighting_payload(row) for row in rows],
            })
    return {"count": len(trails), "trails": trails}


@router.get("/api/member/database/overview")
def member_database_overview(limit: int = Query(default=20, ge=1, le=100)):
    initialise_member_store()
    path = database_path()
    with connect() as db:
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in MEMBER_TABLES
        }
        audit = [dict(row) for row in db.execute(
            "SELECT * FROM member_audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        outbox = [dict(row) for row in db.execute(
            "SELECT * FROM aws_sync_outbox ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()]
        pending = int(db.execute(
            "SELECT COUNT(*) AS n FROM aws_sync_outbox WHERE status='LOCAL_PENDING'"
        ).fetchone()["n"])
    return {
        "database_path": str(path),
        "database_size_bytes": path.stat().st_size if path.exists() else 0,
        "engine": "SQLite WAL",
        "counts": counts,
        "recent_writes": audit,
        "outbox": outbox,
        "pending_aws_records": pending,
        "aws_sync_status": "LOCAL_PENDING" if pending else "UP_TO_DATE_LOCAL",
        "aws_target_mapping": AWS_TABLE_MAP,
        "migration_note": "The outbox keeps each local write ready for a later DynamoDB/S3 sync worker.",
    }


@router.get("/api/member/database/download", include_in_schema=False)
def download_member_database():
    initialise_member_store()
    return FileResponse(database_path(), filename="sentinel_ops_demo.db", media_type="application/vnd.sqlite3")


@router.get("/api/member/face-media/{media_name}", include_in_schema=False)
def face_media(media_name: str):
    target = (FACE_MEDIA_ROOT / Path(media_name).name).resolve()
    if not target.is_relative_to(FACE_MEDIA_ROOT.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="face media not found")
    return FileResponse(target)


@router.delete("/api/member/face-sightings")
def reset_face_sightings():
    initialise_member_store()
    with connect() as db:
        counts = {
            table: db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in (
                "face_sightings", "face_profiles", "member_profile_labels", "member_incidents",
                "member_camera_notifications", "member_audit_log", "aws_sync_outbox",
            )
        }
        db.execute("DELETE FROM member_camera_notifications")
        db.execute("DELETE FROM member_incidents")
        db.execute("DELETE FROM member_profile_labels")
        db.execute("DELETE FROM face_sightings")
        db.execute("DELETE FROM face_profiles")
        db.execute("DELETE FROM member_audit_log")
        db.execute("DELETE FROM aws_sync_outbox")
        _audit(
            db, "member_audit_log", "DEMO-RESET", "RESET", "Member demo operator",
            "Visitor, incident and AWS outbox demo data reset", counts, queue_for_aws=False,
        )
    for path in FACE_MEDIA_ROOT.glob("*.jpg"):
        path.unlink(missing_ok=True)
    return {"removed": counts}
