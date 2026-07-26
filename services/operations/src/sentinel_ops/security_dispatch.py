"""Persistent security-partner patrol and incident-dispatch simulation.

The GradHack demo needs a convincing operational view without pretending that live
security-company GPS feeds or WhatsApp Business credentials are connected.  This
module therefore provides a deterministic Benoni/Lakefield simulation backed by the
same SQLite database used by Member and Claims.

It models three fictional response companies, six patrol units, claims-style hotspot
statistics, fuel-aware route recommendations, incident dispatches, notification
outbox entries and a moving-unit simulation.  A confirmed Member incident can create
a dispatch automatically.  Security users receive only the operational minimum:
where to go, urgency, a reviewed anonymous profile reference and a route.  Member
identity, claim value and raw biometric media are deliberately excluded.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sentinel_ops.storage import connect, database_path

router = APIRouter(tags=["security dispatch"])

BENONI_CENTRE = {"latitude": -26.18848, "longitude": 28.32078}
LAKEFIELD_CENTRE = {"latitude": -26.198055, "longitude": 28.310491}

# The street names are real Benoni/Lakefield roads, while the points and movement are
# deliberately approximate for the demo.  Production routing would use a road graph
# provider and authenticated partner GPS feeds.
ROAD_NODES: list[dict[str, Any]] = [
    {"node_id": "RD-SHER", "street": "Sher Avenue", "latitude": -26.198055, "longitude": 28.310491},
    {"node_id": "RD-LAKEFIELD", "street": "Lakefield Avenue", "latitude": -26.19395, "longitude": 28.30670},
    {"node_id": "RD-SUNNYSIDE", "street": "Sunnyside Avenue", "latitude": -26.19180, "longitude": 28.30930},
    {"node_id": "RD-SHONGWENI", "street": "Shongweni Street", "latitude": -26.20055, "longitude": 28.30720},
    {"node_id": "RD-ATLAS", "street": "Atlas Road", "latitude": -26.19050, "longitude": 28.29820},
    {"node_id": "RD-RACECOURSE", "street": "Racecourse Road", "latitude": -26.19400, "longitude": 28.30110},
    {"node_id": "RD-LAKESIDE", "street": "Lakeside Avenue", "latitude": -26.18975, "longitude": 28.31155},
    {"node_id": "RD-BUNYAN", "street": "Bunyan Street", "latitude": -26.19025, "longitude": 28.31835},
    {"node_id": "RD-TOMJONES", "street": "Tom Jones Street", "latitude": -26.18615, "longitude": 28.32125},
    {"node_id": "RD-FIFTH", "street": "Fifth Avenue", "latitude": -26.17670, "longitude": 28.31920},
    {"node_id": "RD-GREATNORTH", "street": "Great North Road", "latitude": -26.16550, "longitude": 28.31550},
    {"node_id": "RD-PRETORIA", "street": "Pretoria Road", "latitude": -26.15550, "longitude": 28.33050},
    {"node_id": "RD-NORTHRAND", "street": "North Rand Road", "latitude": -26.18170, "longitude": 28.29880},
]
ROAD_BY_ID = {node["node_id"]: node for node in ROAD_NODES}

COMPANY_SEED = [
    {
        "company_id": "SEC-LRF",
        "name": "Lakefield Response Force",
        "short_name": "LRF",
        "dispatch_channel": "WHATSAPP_READY",
        "operations_contact": "+27 00 000 0101",
        "service_area": "Lakefield / Westdene",
        "display_colour": "#00AEEF",
    },
    {
        "company_id": "SEC-ELS",
        "name": "Eastline Security",
        "short_name": "ELS",
        "dispatch_channel": "WHATSAPP_READY",
        "operations_contact": "+27 00 000 0102",
        "service_area": "Benoni Central / Farrarmere",
        "display_colour": "#7A4DB8",
    },
    {
        "company_id": "SEC-CSP",
        "name": "Community Shield Patrol",
        "short_name": "CSP",
        "dispatch_channel": "WHATSAPP_READY",
        "operations_contact": "+27 00 000 0103",
        "service_area": "Northmead / Rynfield",
        "display_colour": "#2E7D4F",
    },
]

UNIT_SEED = [
    {"unit_id": "LRF-12", "company_id": "SEC-LRF", "callsign": "Lake 12", "vehicle_type": "SUV", "fuel_l_per_100km": 10.8, "node_id": "RD-LAKEFIELD", "loop": ["RD-LAKEFIELD", "RD-SUNNYSIDE", "RD-SHER", "RD-SHONGWENI", "RD-RACECOURSE"]},
    {"unit_id": "LRF-18", "company_id": "SEC-LRF", "callsign": "Lake 18", "vehicle_type": "Hatchback", "fuel_l_per_100km": 7.4, "node_id": "RD-ATLAS", "loop": ["RD-ATLAS", "RD-NORTHRAND", "RD-RACECOURSE", "RD-LAKEFIELD"]},
    {"unit_id": "ELS-04", "company_id": "SEC-ELS", "callsign": "East 04", "vehicle_type": "Sedan", "fuel_l_per_100km": 8.1, "node_id": "RD-TOMJONES", "loop": ["RD-TOMJONES", "RD-BUNYAN", "RD-LAKESIDE", "RD-SHER"]},
    {"unit_id": "ELS-09", "company_id": "SEC-ELS", "callsign": "East 09", "vehicle_type": "SUV", "fuel_l_per_100km": 11.2, "node_id": "RD-FIFTH", "loop": ["RD-FIFTH", "RD-TOMJONES", "RD-BUNYAN", "RD-LAKESIDE"]},
    {"unit_id": "CSP-21", "company_id": "SEC-CSP", "callsign": "Shield 21", "vehicle_type": "Bakkie", "fuel_l_per_100km": 12.5, "node_id": "RD-GREATNORTH", "loop": ["RD-GREATNORTH", "RD-FIFTH", "RD-PRETORIA", "RD-GREATNORTH"]},
    {"unit_id": "CSP-25", "company_id": "SEC-CSP", "callsign": "Shield 25", "vehicle_type": "Hatchback", "fuel_l_per_100km": 7.1, "node_id": "RD-PRETORIA", "loop": ["RD-PRETORIA", "RD-GREATNORTH", "RD-FIFTH", "RD-TOMJONES"]},
]

# Illustrative values for the hackathon story.  They are not represented as official
# Benoni crime statistics.  The structure mirrors the existing claims hotspot model.
HOTSPOT_SEED = [
    {"hotspot_id": "BEN-H01", "area": "Lakefield residential corridor", "latitude": -26.198055, "longitude": 28.310491, "priority": 88.0, "incidents_5y": 62, "recent_90d": 7, "dominant_peril": "Home Invasion", "peak_window": "Fri–Sun · 18:00–23:00", "trend": "+12%", "geofence_radius_km": 1.2},
    {"hotspot_id": "BEN-H02", "area": "Benoni CBD", "latitude": -26.18848, "longitude": 28.32078, "priority": 81.0, "incidents_5y": 114, "recent_90d": 12, "dominant_peril": "Vehicle Theft", "peak_window": "Mon–Fri · 16:00–20:00", "trend": "+8%", "geofence_radius_km": 1.4},
    {"hotspot_id": "BEN-H03", "area": "Atlas / Racecourse corridor", "latitude": -26.19190, "longitude": 28.29960, "priority": 74.0, "incidents_5y": 70, "recent_90d": 8, "dominant_peril": "Vehicle Theft", "peak_window": "Thu–Sat · 19:00–00:00", "trend": "+5%", "geofence_radius_km": 1.3},
    {"hotspot_id": "BEN-H04", "area": "Farrarmere", "latitude": -26.17440, "longitude": 28.30730, "priority": 70.0, "incidents_5y": 83, "recent_90d": 9, "dominant_peril": "Vehicle Theft", "peak_window": "Fri–Sun · 17:00–22:00", "trend": "+4%", "geofence_radius_km": 1.5},
    {"hotspot_id": "BEN-H05", "area": "Northmead", "latitude": -26.17120, "longitude": 28.32420, "priority": 66.0, "incidents_5y": 74, "recent_90d": 8, "dominant_peril": "Home Invasion", "peak_window": "Wed–Sun · 20:00–02:00", "trend": "stable", "geofence_radius_km": 1.5},
    {"hotspot_id": "BEN-H06", "area": "Rynfield / Pretoria Road", "latitude": -26.15590, "longitude": 28.33020, "priority": 61.0, "incidents_5y": 58, "recent_90d": 6, "dominant_peril": "Vehicle Theft", "peak_window": "Fri–Sun · 18:00–23:00", "trend": "-3%", "geofence_radius_km": 1.6},
    {"hotspot_id": "BEN-H07", "area": "Westdene / Lakeside", "latitude": -26.18670, "longitude": 28.31190, "priority": 57.0, "incidents_5y": 49, "recent_90d": 5, "dominant_peril": "Home Invasion", "peak_window": "Sat–Sun · 00:00–04:00", "trend": "stable", "geofence_radius_km": 1.1},
]

SECURITY_TABLES = (
    "security_companies",
    "security_units",
    "security_hotspots",
    "security_dispatches",
    "security_notifications",
    "security_activity",
)
AWS_SECURITY_TABLE_MAP = {
    "security_companies": "SentinelSecurityCompanies",
    "security_units": "SentinelSecurityUnits",
    "security_hotspots": "SentinelSecurityHotspots",
    "security_dispatches": "SentinelSecurityDispatches",
    "security_notifications": "SentinelSecurityNotifications",
    "security_activity": "SentinelSecurityActivity",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed
    except (TypeError, ValueError):
        return None


def _live_member_state(db) -> dict[str, Any]:
    """Return active and recent Member incident tracks for the security map.

    A confirmed intruder remains an anonymous operational profile. Every later
    matching sighting at another participating camera extends the same track.
    """
    required = {"member_incidents", "face_sightings", "member_cameras", "face_profiles"}
    if not all(_table_exists(db, table) for table in required):
        return {"tracks": [], "heat_points": [], "active_track_count": 0, "live_match_count": 0}

    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=24)
    incidents = db.execute(
        """
        SELECT i.incident_id, i.profile_id, i.incident_type, i.status, i.started_at,
               i.updated_at, i.expires_at, i.notes, p.anonymous_label,
               c.household AS origin_household, c.suburb AS origin_suburb,
               c.latitude AS origin_latitude, c.longitude AS origin_longitude
        FROM member_incidents i
        JOIN face_profiles p ON p.profile_id=i.profile_id
        JOIN member_cameras c ON c.camera_id=i.origin_camera_id
        ORDER BY i.started_at DESC
        LIMIT 30
        """
    ).fetchall()

    tracks: list[dict[str, Any]] = []
    heat_points: list[dict[str, Any]] = []
    for row in incidents:
        incident = dict(row)
        started = _parse_iso(incident.get("started_at"))
        if incident.get("status") != "ACTIVE" and (not started or started < cutoff):
            continue
        sighting_rows = db.execute(
            """
            SELECT s.sighting_id, s.camera_id, s.captured_at, s.latitude, s.longitude,
                   s.similarity, s.detection_confidence, s.journey_distance_m,
                   s.journey_speed_kmh, s.journey_direction, c.household, c.suburb
            FROM face_sightings s
            JOIN member_cameras c ON c.camera_id=s.camera_id
            WHERE s.profile_id=? AND s.captured_at>=?
            ORDER BY s.captured_at
            """,
            (incident["profile_id"], incident["started_at"]),
        ).fetchall()
        points = [dict(item) for item in sighting_rows]
        if not points:
            points = [{
                "sighting_id": None,
                "camera_id": None,
                "captured_at": incident["started_at"],
                "latitude": incident["origin_latitude"],
                "longitude": incident["origin_longitude"],
                "similarity": 1.0,
                "detection_confidence": 1.0,
                "journey_distance_m": 0.0,
                "journey_speed_kmh": 0.0,
                "journey_direction": "ORIGIN",
                "household": incident["origin_household"],
                "suburb": incident["origin_suburb"],
            }]
        latest = points[-1]
        is_active = incident.get("status") == "ACTIVE"
        tracks.append({
            "incident_id": incident["incident_id"],
            "profile_id": incident["profile_id"],
            "anonymous_label": incident["anonymous_label"],
            "incident_type": incident["incident_type"],
            "status": incident["status"],
            "started_at": incident["started_at"],
            "expires_at": incident.get("expires_at"),
            "origin_household": incident["origin_household"],
            "origin_suburb": incident["origin_suburb"],
            "points": points,
            "latest": latest,
            "match_count": max(0, len(points) - 1),
            "camera_count": len({item.get("camera_id") for item in points if item.get("camera_id")}),
            "is_active": is_active,
        })
        for index, point in enumerate(points):
            captured = _parse_iso(point.get("captured_at"))
            age_minutes = max(0.0, (now - captured).total_seconds() / 60.0) if captured else 180.0
            recency = max(0.2, 1.0 - min(age_minutes, 180.0) / 180.0)
            heat_points.append({
                "incident_id": incident["incident_id"],
                "profile_id": incident["profile_id"],
                "latitude": float(point["latitude"]),
                "longitude": float(point["longitude"]),
                "household": point.get("household"),
                "captured_at": point.get("captured_at"),
                "sequence": index + 1,
                "weight": round(recency * (1.0 if is_active else 0.6), 3),
                "is_latest": index == len(points) - 1,
                "is_active": is_active,
            })
    return {
        "tracks": tracks,
        "heat_points": heat_points,
        "active_track_count": sum(1 for item in tracks if item["is_active"]),
        "live_match_count": sum(item["match_count"] for item in tracks if item["is_active"]),
    }


def _enrich_hotspots_with_live_events(
    hotspots: list[dict[str, Any]], live_state: dict[str, Any]
) -> list[dict[str, Any]]:
    active_tracks = [item for item in live_state.get("tracks", []) if item.get("is_active")]
    heat_points = live_state.get("heat_points", [])
    max_incidents = max((int(item.get("incidents_5y") or 0) for item in hotspots), default=1) or 1
    max_recent = max((int(item.get("recent_90d") or 0) for item in hotspots), default=1) or 1
    max_priority = max((float(item.get("priority") or 0) for item in hotspots), default=1.0) or 1.0
    enriched: list[dict[str, Any]] = []
    for source in hotspots:
        item = dict(source)
        radius = max(0.35, float(item.get("geofence_radius_km") or 1.0))
        nearby = [
            point for point in heat_points
            if _distance_km(
                float(item["latitude"]), float(item["longitude"]),
                float(point["latitude"]), float(point["longitude"]),
            ) <= radius
        ]
        active_incidents = 0
        for track in active_tracks:
            latest = track.get("latest") or {}
            if latest and _distance_km(
                float(item["latitude"]), float(item["longitude"]),
                float(latest.get("latitude") or 0), float(latest.get("longitude") or 0),
            ) <= radius:
                active_incidents += 1
        base_priority = float(item.get("priority") or 0)
        live_boost = min(24.0, active_incidents * 10.0 + len(nearby) * 2.5)
        current_priority = min(100.0, base_priority + live_boost)
        recent_count = int(item.get("recent_90d") or 0) + len(nearby)
        incident_count = int(item.get("incidents_5y") or 0)
        historical_component = 0.45 * (incident_count / max_incidents)
        recent_component = 0.20 * (recent_count / max(max_recent, 1))
        priority_component = 0.35 * (current_priority / max(max_priority, 1.0))
        heat_intensity = max(0.08, min(1.0, historical_component + recent_component + priority_component))
        heat_radius_m = round(260.0 + 1040.0 * math.sqrt(heat_intensity), 1)
        heat_band = "VERY HIGH" if heat_intensity >= 0.82 else "HIGH" if heat_intensity >= 0.62 else "MEDIUM" if heat_intensity >= 0.40 else "LOW"
        item.update({
            "base_priority": round(base_priority, 1),
            "priority": round(current_priority, 1),
            "live_priority_boost": round(live_boost, 1),
            "live_event_count": len(nearby),
            "active_incident_count": active_incidents,
            "recent_90d": recent_count,
            "heat_intensity": round(heat_intensity, 3),
            "heat_radius_m": heat_radius_m,
            "heat_band": heat_band,
            "heat_inputs": {
                "incidents_5y": incident_count,
                "recent_90d": recent_count,
                "current_priority": round(current_priority, 1),
                "live_event_count": len(nearby),
            },
            "source_note": (
                f"{item.get('source_note', '')} Live Member incident and repeat-camera matches "
                "are layered onto the baseline at request time. Heat intensity is calculated from "
                "historical incidents, recent events and current risk priority."
            ).strip(),
        })
        enriched.append(item)
    return sorted(enriched, key=lambda value: float(value.get("priority") or 0), reverse=True)


def _distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1.0 - h)))


def _route_distance(points: list[dict[str, Any]]) -> float:
    total = 0.0
    for left, right in zip(points, points[1:]):
        total += _distance_km(
            float(left["latitude"]), float(left["longitude"]),
            float(right["latitude"]), float(right["longitude"]),
        )
    return total


def _nearest_road(latitude: float, longitude: float) -> dict[str, Any]:
    return min(
        ROAD_NODES,
        key=lambda node: _distance_km(latitude, longitude, node["latitude"], node["longitude"]),
    )


def _activity(db, event_type: str, title: str, detail: str, *, actor: str = "Sentinel Dispatch", payload: Any | None = None) -> str:
    activity_id = f"SACT-{uuid.uuid4().hex[:12].upper()}"
    created_at = _now()
    payload_json = _safe_json(payload or {})
    db.execute(
        """
        INSERT INTO security_activity(activity_id, event_type, title, detail, actor, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (activity_id, event_type, title, detail, actor, payload_json, created_at),
    )
    # Shared audit/outbox tables give the user one visible local-to-AWS lineage.
    try:
        db.execute(
            """
            INSERT INTO member_audit_log(
                audit_id, table_name, record_id, action, actor, summary, payload_json, created_at
            ) VALUES (?, 'security_activity', ?, ?, ?, ?, ?, ?)
            """,
            (f"AUD-{uuid.uuid4().hex[:12].upper()}", activity_id, event_type, actor, title, payload_json, created_at),
        )
        db.execute(
            """
            INSERT INTO aws_sync_outbox(
                outbox_id, entity_type, entity_id, action, payload_json, status, created_at
            ) VALUES (?, 'security_activity', ?, ?, ?, 'LOCAL_PENDING', ?)
            """,
            (f"OUT-{uuid.uuid4().hex[:12].upper()}", activity_id, event_type, payload_json, created_at),
        )
    except Exception:
        pass
    return activity_id


def _queue_entity(db, table: str, entity_id: str, action: str, payload: Any, actor: str = "Sentinel Dispatch") -> None:
    payload_json = _safe_json(payload)
    created_at = _now()
    try:
        db.execute(
            """
            INSERT INTO member_audit_log(
                audit_id, table_name, record_id, action, actor, summary, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"AUD-{uuid.uuid4().hex[:12].upper()}", table, entity_id, action, actor,
                f"{action} {table} {entity_id}", payload_json, created_at,
            ),
        )
        db.execute(
            """
            INSERT INTO aws_sync_outbox(
                outbox_id, entity_type, entity_id, action, payload_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'LOCAL_PENDING', ?)
            """,
            (f"OUT-{uuid.uuid4().hex[:12].upper()}", table, entity_id, action, payload_json, created_at),
        )
    except Exception:
        pass


def initialise_security_store() -> None:
    # The Member initialiser guarantees the common audit and AWS-outbox tables exist.
    try:
        from sentinel_ops.member_mesh import initialise_member_store
        initialise_member_store()
    except Exception:
        pass
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS security_companies (
                company_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                short_name TEXT NOT NULL,
                dispatch_channel TEXT NOT NULL,
                operations_contact TEXT,
                service_area TEXT,
                display_colour TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS security_units (
                unit_id TEXT PRIMARY KEY,
                company_id TEXT NOT NULL,
                callsign TEXT NOT NULL,
                vehicle_type TEXT NOT NULL,
                fuel_l_per_100km REAL NOT NULL,
                status TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                current_street TEXT NOT NULL,
                route_json TEXT,
                route_index INTEGER NOT NULL DEFAULT 0,
                route_kind TEXT NOT NULL DEFAULT 'PATROL_LOOP',
                active_dispatch_id TEXT,
                assigned_hotspot_id TEXT,
                last_tick_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES security_companies(company_id)
            );
            CREATE INDEX IF NOT EXISTS idx_security_units_company ON security_units(company_id, status);
            CREATE TABLE IF NOT EXISTS security_hotspots (
                hotspot_id TEXT PRIMARY KEY,
                area TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                priority REAL NOT NULL,
                incidents_5y INTEGER NOT NULL,
                recent_90d INTEGER NOT NULL,
                dominant_peril TEXT NOT NULL,
                peak_window TEXT NOT NULL,
                trend TEXT NOT NULL,
                geofence_radius_km REAL NOT NULL,
                source_note TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS security_dispatches (
                dispatch_id TEXT PRIMARY KEY,
                member_incident_id TEXT,
                incident_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL,
                address TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                anonymous_profile_id TEXT,
                selected_unit_id TEXT,
                backup_unit_ids_json TEXT,
                route_json TEXT,
                distance_km REAL,
                eta_minutes REAL,
                estimated_fuel_litres REAL,
                protected_risk REAL,
                coverage_percent REAL,
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                closed_at TEXT,
                FOREIGN KEY(selected_unit_id) REFERENCES security_units(unit_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_security_dispatch_member_incident
                ON security_dispatches(member_incident_id) WHERE member_incident_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS security_notifications (
                notification_id TEXT PRIMARY KEY,
                dispatch_id TEXT NOT NULL,
                company_id TEXT,
                unit_id TEXT,
                channel TEXT NOT NULL,
                recipient TEXT,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_payload_json TEXT,
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                FOREIGN KEY(dispatch_id) REFERENCES security_dispatches(dispatch_id)
            );
            CREATE INDEX IF NOT EXISTS idx_security_notifications_dispatch ON security_notifications(dispatch_id, created_at);
            CREATE TABLE IF NOT EXISTS security_activity (
                activity_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_security_activity_created ON security_activity(created_at DESC);
            """
        )
        now = _now()
        for company in COMPANY_SEED:
            db.execute(
                """
                INSERT INTO security_companies(
                    company_id, name, short_name, dispatch_channel, operations_contact,
                    service_area, display_colour, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    name=excluded.name, short_name=excluded.short_name,
                    dispatch_channel=excluded.dispatch_channel,
                    operations_contact=excluded.operations_contact,
                    service_area=excluded.service_area, display_colour=excluded.display_colour
                """,
                (
                    company["company_id"], company["name"], company["short_name"],
                    company["dispatch_channel"], company["operations_contact"],
                    company["service_area"], company["display_colour"], now,
                ),
            )
        for hotspot in HOTSPOT_SEED:
            db.execute(
                """
                INSERT INTO security_hotspots(
                    hotspot_id, area, latitude, longitude, priority, incidents_5y,
                    recent_90d, dominant_peril, peak_window, trend, geofence_radius_km,
                    source_note, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hotspot_id) DO UPDATE SET
                    area=excluded.area, latitude=excluded.latitude, longitude=excluded.longitude,
                    priority=excluded.priority, incidents_5y=excluded.incidents_5y,
                    recent_90d=excluded.recent_90d, dominant_peril=excluded.dominant_peril,
                    peak_window=excluded.peak_window, trend=excluded.trend,
                    geofence_radius_km=excluded.geofence_radius_km,
                    source_note=excluded.source_note, updated_at=excluded.updated_at
                """,
                (
                    hotspot["hotspot_id"], hotspot["area"], hotspot["latitude"], hotspot["longitude"],
                    hotspot["priority"], hotspot["incidents_5y"], hotspot["recent_90d"],
                    hotspot["dominant_peril"], hotspot["peak_window"], hotspot["trend"],
                    hotspot["geofence_radius_km"],
                    "Illustrative GradHack statistics shaped like the claims hotspot pipeline; not live Benoni crime data.",
                    now,
                ),
            )
        for unit in UNIT_SEED:
            node = ROAD_BY_ID[unit["node_id"]]
            route = [ROAD_BY_ID[item] for item in unit["loop"]]
            db.execute(
                """
                INSERT INTO security_units(
                    unit_id, company_id, callsign, vehicle_type, fuel_l_per_100km,
                    status, latitude, longitude, current_street, route_json, route_index,
                    route_kind, active_dispatch_id, assigned_hotspot_id, last_tick_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'AVAILABLE', ?, ?, ?, ?, 0, 'PATROL_LOOP', NULL, NULL, ?, ?)
                ON CONFLICT(unit_id) DO NOTHING
                """,
                (
                    unit["unit_id"], unit["company_id"], unit["callsign"], unit["vehicle_type"],
                    unit["fuel_l_per_100km"], node["latitude"], node["longitude"], node["street"],
                    _safe_json(route), now, now,
                ),
            )


def _hotspots(db, live_state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    baseline = [dict(row) for row in db.execute("SELECT * FROM security_hotspots ORDER BY priority DESC").fetchall()]
    return _enrich_hotspots_with_live_events(baseline, live_state or _live_member_state(db))


def _companies(db) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM security_companies ORDER BY name").fetchall()
    units = db.execute("SELECT company_id, status, COUNT(*) AS n FROM security_units GROUP BY company_id, status").fetchall()
    by_company: dict[str, dict[str, int]] = {}
    for row in units:
        by_company.setdefault(row["company_id"], {})[row["status"]] = int(row["n"])
    return [dict(row) | {"unit_status_counts": by_company.get(row["company_id"], {})} for row in rows]


def _unit_payload(row: Any, company_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = dict(row)
    item["route"] = _json(item.pop("route_json", None), [])
    item["company"] = company_by_id.get(item["company_id"])
    return item


def _units(db) -> list[dict[str, Any]]:
    companies = {row["company_id"]: dict(row) for row in db.execute("SELECT * FROM security_companies").fetchall()}
    return [_unit_payload(row, companies) for row in db.execute("SELECT * FROM security_units ORDER BY company_id, unit_id").fetchall()]


def _dispatch_payload(db, dispatch_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM security_dispatches WHERE dispatch_id=?", (dispatch_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["route"] = _json(item.pop("route_json", None), [])
    item["backup_unit_ids"] = _json(item.pop("backup_unit_ids_json", None), [])
    item["notifications"] = [dict(r) | {"provider_payload": _json(r["provider_payload_json"], {})}
                             for r in db.execute(
                                 "SELECT * FROM security_notifications WHERE dispatch_id=? ORDER BY created_at",
                                 (dispatch_id,),
                             ).fetchall()]
    return item


def _coverage_assignments(units: list[dict[str, Any]], hotspots: list[dict[str, Any]], exclude_unit_id: str | None = None) -> set[str]:
    covered: set[str] = set()
    for unit in units:
        if unit["unit_id"] == exclude_unit_id or unit["status"] == "OFFLINE":
            continue
        explicit = unit.get("assigned_hotspot_id")
        if explicit:
            covered.add(explicit)
            continue
        nearest = min(
            hotspots,
            key=lambda h: _distance_km(unit["latitude"], unit["longitude"], h["latitude"], h["longitude"]),
        )
        if _distance_km(unit["latitude"], unit["longitude"], nearest["latitude"], nearest["longitude"]) <= 2.5:
            covered.add(nearest["hotspot_id"])
    return covered


def _route_for_unit(db, unit_id: str, *, max_stops: int = 4, incident: dict[str, Any] | None = None) -> dict[str, Any]:
    unit_row = db.execute("SELECT * FROM security_units WHERE unit_id=?", (unit_id,)).fetchone()
    if not unit_row:
        raise HTTPException(status_code=404, detail="security unit not found")
    unit = dict(unit_row)
    hotspots = _hotspots(db)
    all_units = [dict(row) for row in db.execute("SELECT * FROM security_units").fetchall()]
    already_covered = _coverage_assignments(all_units, hotspots, exclude_unit_id=unit_id)

    current = {"latitude": float(unit["latitude"]), "longitude": float(unit["longitude"]), "street": unit["current_street"], "kind": "UNIT_START"}
    points: list[dict[str, Any]] = [current]
    selected: list[dict[str, Any]] = []
    protected_risk = 0.0

    if incident:
        road = _nearest_road(float(incident["latitude"]), float(incident["longitude"]))
        points.append({**road, "kind": "APPROACH"})
        points.append({
            "latitude": float(incident["latitude"]), "longitude": float(incident["longitude"]),
            "street": incident["address"], "kind": "INCIDENT", "dispatch_id": incident.get("dispatch_id"),
        })
        current = points[-1]

    remaining = hotspots[:]
    while remaining and len(selected) < max_stops:
        def score(h: dict[str, Any]) -> float:
            distance = _distance_km(current["latitude"], current["longitude"], h["latitude"], h["longitude"])
            overlap_factor = 0.45 if h["hotspot_id"] in already_covered else 1.0
            return float(h["priority"]) * overlap_factor / max(0.45, distance)

        best = max(remaining, key=score)
        remaining.remove(best)
        selected.append(best)
        protected_risk += float(best["priority"])
        points.append({
            "latitude": float(best["latitude"]), "longitude": float(best["longitude"]),
            "street": best["area"], "kind": "HOTSPOT", "hotspot_id": best["hotspot_id"],
            "priority": float(best["priority"]), "already_covered": best["hotspot_id"] in already_covered,
        })
        current = points[-1]

    distance = _route_distance(points)
    fuel = distance * float(unit["fuel_l_per_100km"]) / 100.0
    total_risk = sum(float(h["priority"]) for h in hotspots) or 1.0
    baseline_order = sorted(
        hotspots,
        key=lambda h: _distance_km(unit["latitude"], unit["longitude"], h["latitude"], h["longitude"]),
    )[:max_stops]
    baseline_points = [points[0]] + [
        {"latitude": float(h["latitude"]), "longitude": float(h["longitude"]), "street": h["area"]}
        for h in baseline_order
    ]
    baseline_distance = _route_distance(baseline_points)
    baseline_risk = sum(float(h["priority"]) for h in baseline_order)
    baseline_efficiency = baseline_risk / max(0.001, baseline_distance)
    optimised_efficiency = protected_risk / max(0.001, distance)
    distance_saved = max(0.0, baseline_distance - distance)
    fuel_saved = distance_saved * float(unit["fuel_l_per_100km"]) / 100.0

    return {
        "unit_id": unit_id,
        "route_kind": "INCIDENT_RESPONSE" if incident else "OPTIMISED_PATROL",
        "points": points,
        "hotspot_ids": [h["hotspot_id"] for h in selected],
        "distance_km": round(distance, 2),
        "estimated_fuel_litres": round(fuel, 3),
        "protected_risk": round(protected_risk, 1),
        "coverage_percent": round(100.0 * protected_risk / total_risk, 1),
        "protected_risk_per_km": round(protected_risk / max(0.001, distance), 2),
        "overlap_avoided": sum(1 for h in selected if h["hotspot_id"] not in already_covered),
        "already_covered_hotspots": sorted(already_covered),
        "baseline": {
            "distance_km": round(baseline_distance, 2),
            "protected_risk": round(baseline_risk, 1),
            "protected_risk_per_km": round(baseline_efficiency, 2),
        },
        "distance_saved_km": round(distance_saved, 2),
        "fuel_saved_litres": round(fuel_saved, 3),
        "efficiency_improvement_percent": round(
            100.0 * (optimised_efficiency - baseline_efficiency) / max(0.001, baseline_efficiency), 1
        ),
        "method": "Risk-weighted greedy selection with overlap discount against other active units, followed by fuel accounting.",
    }


def _best_units_for_incident(db, latitude: float, longitude: float) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT u.*, c.name AS company_name, c.operations_contact, c.short_name
        FROM security_units u JOIN security_companies c ON c.company_id=u.company_id
        WHERE u.status IN ('AVAILABLE','PATROLLING')
        """
    ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        distance = _distance_km(float(item["latitude"]), float(item["longitude"]), latitude, longitude)
        # Estimated urban response speed for the simulation only.
        eta = max(1.0, distance / 38.0 * 60.0 + 1.2)
        fuel = distance * float(item["fuel_l_per_100km"]) / 100.0
        # Lower is better: ETA dominates, with a smaller fuel component.
        score = eta + fuel * 2.2
        candidates.append(item | {"distance_km": distance, "eta_minutes": eta, "fuel_litres": fuel, "dispatch_score": score})
    candidates.sort(key=lambda item: item["dispatch_score"])
    return candidates


def _create_notification(db, dispatch: dict[str, Any], unit: dict[str, Any], *, primary: bool) -> dict[str, Any]:
    notification_id = f"SNOTE-{uuid.uuid4().hex[:11].upper()}"
    role = "PRIMARY RESPONSE" if primary else "BACKUP / AREA COVER"
    message = (
        f"MZANSIMESH {dispatch['priority']} ALERT | {role}\n"
        f"{dispatch['incident_type'].replace('_', ' ').title()} · {dispatch['address']}\n"
        f"ETA {unit['eta_minutes']:.1f} min · {unit['distance_km']:.2f} km\n"
        f"Reference {dispatch['dispatch_id']} · anonymous evidence only\n"
        "Acknowledge in MzansiMesh before dispatch. Do not infer identity from biometric matching."
    )
    provider = {
        "provider": "WhatsApp Business API placeholder",
        "to": unit.get("operations_contact"),
        "template": "mzansimesh_security_dispatch_v1",
        "parameters": {
            "dispatch_id": dispatch["dispatch_id"],
            "address": dispatch["address"],
            "priority": dispatch["priority"],
            "eta_minutes": round(float(unit["eta_minutes"]), 1),
        },
        "production_note": "Replace local queue with an approved WhatsApp Business provider/webhook.",
    }
    db.execute(
        """
        INSERT INTO security_notifications(
            notification_id, dispatch_id, company_id, unit_id, channel, recipient,
            message, status, provider_payload_json, created_at
        ) VALUES (?, ?, ?, ?, 'WHATSAPP_READY', ?, ?, 'QUEUED_LOCAL', ?, ?)
        """,
        (
            notification_id, dispatch["dispatch_id"], unit["company_id"], unit["unit_id"],
            unit.get("operations_contact"), message, _safe_json(provider), _now(),
        ),
    )
    _queue_entity(db, "security_notifications", notification_id, "INSERT", provider)
    return {"notification_id": notification_id, "message": message, "status": "QUEUED_LOCAL"}


def create_dispatch_for_member_incident(db, incident_id: str, *, actor: str = "Sentinel incident bridge") -> dict[str, Any]:
    """Create (or return) the security dispatch for one confirmed Member incident.

    This function accepts an existing SQLite connection so Member can call it inside
    the same transaction that creates the incident and neighbouring-camera watch.
    """
    # The caller normally initialises the store first; create tables defensively when
    # invoked by an API endpoint with a fresh database.
    existing = db.execute(
        "SELECT dispatch_id, status FROM security_dispatches WHERE member_incident_id=?", (incident_id,)
    ).fetchone() if _table_exists(db, "security_dispatches") else None
    if existing and existing["status"] not in {"CLOSED", "CANCELLED"}:
        payload = _dispatch_payload(db, existing["dispatch_id"])
        assert payload is not None
        return payload
    if existing:
        stale_id = existing["dispatch_id"]
        db.execute(
            "UPDATE security_units SET status='AVAILABLE', active_dispatch_id=NULL, route_json='[]', route_index=0, route_kind='PATROL' WHERE active_dispatch_id=?",
            (stale_id,),
        )
        db.execute("DELETE FROM security_notifications WHERE dispatch_id=?", (stale_id,))
        db.execute("DELETE FROM security_dispatches WHERE dispatch_id=?", (stale_id,))
        _activity(
            db,
            "DISPATCH_REACTIVATED",
            "Closed response reopened after repeat match",
            f"Previous dispatch {stale_id} was replaced because the confirmed intruder profile was detected again.",
            actor=actor,
            payload={"incident_id": incident_id, "previous_dispatch_id": stale_id},
        )

    incident = db.execute(
        """
        SELECT i.*, c.household, c.suburb, c.latitude, c.longitude
        FROM member_incidents i
        JOIN member_cameras c ON c.camera_id=i.origin_camera_id
        WHERE i.incident_id=?
        """,
        (incident_id,),
    ).fetchone()
    if not incident:
        raise HTTPException(status_code=404, detail="member incident not found")

    candidates = _best_units_for_incident(db, float(incident["latitude"]), float(incident["longitude"]))
    if not candidates:
        raise HTTPException(status_code=409, detail="no simulated security units are available")
    primary = candidates[0]
    backups: list[dict[str, Any]] = []
    used_companies = {primary["company_id"]}
    for candidate in candidates[1:]:
        if candidate["company_id"] not in used_companies:
            backups.append(candidate)
            used_companies.add(candidate["company_id"])
        if len(backups) >= 2:
            break

    dispatch_id = f"DSP-{uuid.uuid4().hex[:10].upper()}"
    priority = "CRITICAL" if incident["incident_type"] in {"HOME_INVASION", "VEHICLE_THEFT", "FORCED_ENTRY", "ATTEMPTED_VEHICLE_THEFT"} else "HIGH"
    address = f"{incident['household']}, {incident['suburb']}"
    incident_stub = {
        "dispatch_id": dispatch_id,
        "latitude": float(incident["latitude"]),
        "longitude": float(incident["longitude"]),
        "address": address,
    }
    route = _route_for_unit(db, primary["unit_id"], max_stops=3, incident=incident_stub)
    created_at = _now()
    dispatch = {
        "dispatch_id": dispatch_id,
        "member_incident_id": incident_id,
        "incident_type": incident["incident_type"],
        "priority": priority,
        "status": "AWAITING_ACKNOWLEDGEMENT",
        "address": address,
        "latitude": float(incident["latitude"]),
        "longitude": float(incident["longitude"]),
        "anonymous_profile_id": incident["profile_id"],
        "selected_unit_id": primary["unit_id"],
        "backup_unit_ids": [item["unit_id"] for item in backups],
        "route": route["points"],
        "distance_km": round(float(primary["distance_km"]), 2),
        "eta_minutes": round(float(primary["eta_minutes"]), 1),
        "estimated_fuel_litres": round(float(primary["fuel_litres"]), 3),
        "protected_risk": route["protected_risk"],
        "coverage_percent": route["coverage_percent"],
        "created_at": created_at,
    }
    db.execute(
        """
        INSERT INTO security_dispatches(
            dispatch_id, member_incident_id, incident_type, priority, status, address,
            latitude, longitude, anonymous_profile_id, selected_unit_id,
            backup_unit_ids_json, route_json, distance_km, eta_minutes,
            estimated_fuel_litres, protected_risk, coverage_percent, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dispatch_id, incident_id, incident["incident_type"], priority,
            "AWAITING_ACKNOWLEDGEMENT", address, float(incident["latitude"]),
            float(incident["longitude"]), incident["profile_id"], primary["unit_id"],
            _safe_json(dispatch["backup_unit_ids"]), _safe_json(route["points"]),
            dispatch["distance_km"], dispatch["eta_minutes"], dispatch["estimated_fuel_litres"],
            dispatch["protected_risk"], dispatch["coverage_percent"], created_at,
        ),
    )
    db.execute(
        """
        UPDATE security_units SET status='DISPATCH_PENDING', active_dispatch_id=?, route_json=?,
            route_index=0, route_kind='INCIDENT_RESPONSE', updated_at=? WHERE unit_id=?
        """,
        (dispatch_id, _safe_json(route["points"]), created_at, primary["unit_id"]),
    )
    for backup in backups:
        db.execute(
            "UPDATE security_units SET status='BACKUP_ALERTED', active_dispatch_id=?, updated_at=? WHERE unit_id=?",
            (dispatch_id, created_at, backup["unit_id"]),
        )
    _create_notification(db, dispatch, primary, primary=True)
    for backup in backups:
        _create_notification(db, dispatch, backup, primary=False)
    _activity(
        db, "INCIDENT_DISPATCH_CREATED", "Nearest security partners alerted",
        f"{primary['callsign']} selected at {dispatch['distance_km']:.2f} km; {len(backups)} cross-company backups queued.",
        actor=actor, payload=dispatch,
    )
    _queue_entity(db, "security_dispatches", dispatch_id, "INSERT", dispatch, actor)
    payload = _dispatch_payload(db, dispatch_id)
    assert payload is not None
    return payload


def queue_repeat_intruder_notifications(
    db,
    incident_id: str,
    *,
    household: str,
    sighting_id: str,
    actor: str = "MzansiMesh repeat matcher",
) -> dict[str, Any]:
    """Queue a fresh control-room notification for one repeat intruder sighting.

    The original dispatch remains intact. A new notification is added for the
    primary and backup units so the security tab visibly receives every repeat
    match instead of only the first incident confirmation.
    """
    dispatch = create_dispatch_for_member_incident(db, incident_id, actor=actor)
    dispatch_id = dispatch["dispatch_id"]
    duplicate = db.execute(
        "SELECT 1 FROM security_notifications WHERE dispatch_id=? AND message LIKE ? LIMIT 1",
        (dispatch_id, f"%{sighting_id}%"),
    ).fetchone()
    if duplicate:
        return dispatch

    unit_ids = [dispatch.get("selected_unit_id"), *(dispatch.get("backup_unit_ids") or [])]
    unit_ids = [item for item in unit_ids if item]
    now = _now()
    for unit_id in unit_ids:
        row = db.execute(
            """
            SELECT u.*, c.name AS company_name, c.operations_contact, c.short_name
            FROM security_units u JOIN security_companies c ON c.company_id=u.company_id
            WHERE u.unit_id=?
            """,
            (unit_id,),
        ).fetchone()
        if not row:
            continue
        unit = dict(row)
        notification_id = f"SNOTE-{uuid.uuid4().hex[:11].upper()}"
        message = (
            "MZANSIMESH REPEAT INTRUDER ALERT\n"
            f"Active-watch match at {household}\n"
            f"Dispatch {dispatch_id} | sighting {sighting_id}\n"
            "Neighbour cameras remain armed. Review the updated movement trail and acknowledge in MzansiMesh."
        )
        provider = {
            "provider": "WhatsApp Business API placeholder",
            "to": unit.get("operations_contact"),
            "template": "mzansimesh_repeat_intruder_v1",
            "parameters": {
                "dispatch_id": dispatch_id,
                "sighting_id": sighting_id,
                "household": household,
            },
        }
        db.execute(
            """
            INSERT INTO security_notifications(
                notification_id, dispatch_id, company_id, unit_id, channel, recipient,
                message, status, provider_payload_json, created_at
            ) VALUES (?, ?, ?, ?, 'WHATSAPP_READY', ?, ?, 'QUEUED_LOCAL', ?, ?)
            """,
            (
                notification_id, dispatch_id, unit["company_id"], unit_id,
                unit.get("operations_contact"), message, _safe_json(provider), now,
            ),
        )
        _queue_entity(db, "security_notifications", notification_id, "INSERT", provider, actor)
    _activity(
        db,
        "REPEAT_INTRUDER_ALERTED",
        "Repeat intruder match sent to control room",
        f"Active-watch match at {household}; security notifications queued for {len(unit_ids)} unit(s).",
        actor=actor,
        payload={"incident_id": incident_id, "dispatch_id": dispatch_id, "sighting_id": sighting_id, "household": household},
    )
    return _dispatch_payload(db, dispatch_id) or dispatch


def close_dispatch_for_member_incident(db, incident_id: str, *, actor: str = "Sentinel incident bridge") -> dict[str, Any] | None:
    row = db.execute(
        "SELECT dispatch_id FROM security_dispatches WHERE member_incident_id=? AND status NOT IN ('CLOSED','CANCELLED')",
        (incident_id,),
    ).fetchone() if _table_exists(db, "security_dispatches") else None
    if not row:
        return None
    return _close_dispatch_in_db(db, row["dispatch_id"], actor=actor, reason="Member incident closed")


def _close_dispatch_in_db(db, dispatch_id: str, *, actor: str, reason: str) -> dict[str, Any]:
    dispatch = _dispatch_payload(db, dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="dispatch not found")
    now = _now()
    db.execute(
        "UPDATE security_dispatches SET status='CLOSED', closed_at=? WHERE dispatch_id=?",
        (now, dispatch_id),
    )
    db.execute(
        "UPDATE security_notifications SET status=CASE WHEN status='DELIVERED_DEMO' THEN status ELSE 'CLOSED' END WHERE dispatch_id=?",
        (dispatch_id,),
    )
    for unit_id in [dispatch.get("selected_unit_id"), *(dispatch.get("backup_unit_ids") or [])]:
        if not unit_id:
            continue
        seed = next((item for item in UNIT_SEED if item["unit_id"] == unit_id), None)
        if not seed:
            db.execute(
                "UPDATE security_units SET status='AVAILABLE', active_dispatch_id=NULL, route_kind='PATROL_LOOP', updated_at=? WHERE unit_id=?",
                (now, unit_id),
            )
            continue
        route = [ROAD_BY_ID[item] for item in seed["loop"]]
        db.execute(
            """
            UPDATE security_units SET status='AVAILABLE', active_dispatch_id=NULL, route_json=?,
                route_index=0, route_kind='PATROL_LOOP', assigned_hotspot_id=NULL, updated_at=?
            WHERE unit_id=?
            """,
            (_safe_json(route), now, unit_id),
        )
    _activity(
        db, "DISPATCH_CLOSED", "Security dispatch closed",
        f"{dispatch_id} closed: {reason}.", actor=actor,
        payload={"dispatch_id": dispatch_id, "reason": reason},
    )
    _queue_entity(db, "security_dispatches", dispatch_id, "UPDATE", {"status": "CLOSED", "closed_at": now}, actor)
    payload = _dispatch_payload(db, dispatch_id)
    assert payload is not None
    return payload


def _reconcile_dispatches(db) -> None:
    if not _table_exists(db, "security_dispatches") or not _table_exists(db, "member_incidents"):
        return
    rows = db.execute(
        """
        SELECT d.dispatch_id, d.member_incident_id
        FROM security_dispatches d
        JOIN member_incidents i ON i.incident_id=d.member_incident_id
        WHERE d.status NOT IN ('CLOSED','CANCELLED') AND i.status!='ACTIVE'
        """
    ).fetchall()
    for row in rows:
        _close_dispatch_in_db(db, row["dispatch_id"], actor="Sentinel reconciliation", reason="Member watch is no longer active")


def _table_exists(db, table: str) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


class TickIn(BaseModel):
    steps: int = Field(default=1, ge=1, le=10)


class RouteAssignIn(BaseModel):
    max_stops: int = Field(default=4, ge=1, le=7)
    dispatch_id: str | None = None


class AcknowledgeIn(BaseModel):
    acknowledged_by: str = "Demo control room"


class NotificationSendIn(BaseModel):
    sent_by: str = "Demo control room"


class DispatchCloseIn(BaseModel):
    closed_by: str = "Demo control room"
    reason: str = "Control room closed the response"


@router.get("/api/security/operations")
def security_operations():
    initialise_security_store()
    with connect() as db:
        _reconcile_dispatches(db)
        companies = _companies(db)
        units = _units(db)
        live_state = _live_member_state(db)
        hotspots = _hotspots(db, live_state)
        dispatch_ids = [row["dispatch_id"] for row in db.execute(
            "SELECT dispatch_id FROM security_dispatches WHERE status NOT IN ('CLOSED','CANCELLED') ORDER BY created_at DESC"
        ).fetchall()]
        dispatches = [_dispatch_payload(db, item) for item in dispatch_ids]
        notifications = [dict(row) | {"provider_payload": _json(row["provider_payload_json"], {})}
                         for row in db.execute(
                             "SELECT * FROM security_notifications ORDER BY created_at DESC LIMIT 40"
                         ).fetchall()]
        total_priority = sum(float(h["priority"]) for h in hotspots) or 1.0
        covered = _coverage_assignments(units, hotspots)
        covered_priority = sum(float(h["priority"]) for h in hotspots if h["hotspot_id"] in covered)
        available = sum(1 for unit in units if unit["status"] in {"AVAILABLE", "PATROLLING"})
        recent_activity = [dict(row) for row in db.execute(
            "SELECT * FROM security_activity ORDER BY created_at DESC LIMIT 30"
        ).fetchall()]
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in SECURITY_TABLES
        }
        outbox = int(db.execute(
            "SELECT COUNT(*) AS n FROM aws_sync_outbox WHERE status='LOCAL_PENDING'"
        ).fetchone()["n"]) if _table_exists(db, "aws_sync_outbox") else 0
    return {
        "pilot": "Benoni / Lakefield",
        "centre": BENONI_CENTRE,
        "companies": companies,
        "units": units,
        "hotspots": hotspots,
        "dispatches": [item for item in dispatches if item],
        "notifications": notifications,
        "road_nodes": ROAD_NODES,
        "statistics": {
            "companies_connected": len(companies),
            "units_total": len(units),
            "units_available": available,
            "active_dispatches": len(dispatch_ids),
            "hotspots_covered": len(covered),
            "hotspots_total": len(hotspots),
            "risk_coverage_percent": round(100.0 * covered_priority / total_priority, 1),
            "active_intruder_tracks": live_state["active_track_count"],
            "live_camera_matches": live_state["live_match_count"],
        },
        "live_tracks": live_state["tracks"],
        "live_heat_points": live_state["heat_points"],
        "database": {
            "engine": "SQLite",
            "path": str(database_path()),
            "tables": counts,
            "aws_outbox_pending": outbox,
        },
        "activity": recent_activity,
        "simulation_note": "Partner GPS and WhatsApp delivery are simulated for the POC. Member incident tracks and repeat-camera matches are read live from SQLite and layered onto the map and hotspot priorities.",
        "privacy_note": "Security receives operational location and priority only; member names, claim amounts and raw biometric media are excluded.",
    }


@router.get("/api/security/units/{unit_id}/route-preview")
def preview_unit_route(unit_id: str, max_stops: int = Query(default=4, ge=1, le=7), dispatch_id: str | None = None):
    initialise_security_store()
    with connect() as db:
        incident = _dispatch_payload(db, dispatch_id) if dispatch_id else None
        return _route_for_unit(db, unit_id, max_stops=max_stops, incident=incident)


@router.post("/api/security/units/{unit_id}/assign-route")
def assign_unit_route(unit_id: str, body: RouteAssignIn):
    initialise_security_store()
    with connect() as db:
        incident = _dispatch_payload(db, body.dispatch_id) if body.dispatch_id else None
        route = _route_for_unit(db, unit_id, max_stops=body.max_stops, incident=incident)
        status = "RESPONDING" if incident else "PATROLLING"
        db.execute(
            """
            UPDATE security_units SET route_json=?, route_index=0, route_kind=?, status=?,
                active_dispatch_id=COALESCE(?, active_dispatch_id), assigned_hotspot_id=?, updated_at=?
            WHERE unit_id=?
            """,
            (
                _safe_json(route["points"]), route["route_kind"], status,
                body.dispatch_id, route["hotspot_ids"][0] if route["hotspot_ids"] else None,
                _now(), unit_id,
            ),
        )
        _activity(
            db, "ROUTE_ASSIGNED", "Optimised route assigned",
            f"{unit_id}: {route['distance_km']} km, {route['coverage_percent']}% risk coverage.",
            payload=route,
        )
        _queue_entity(db, "security_units", unit_id, "UPDATE", route)
    return route | {"assigned": True}


@router.post("/api/security/simulation/tick")
def tick_security_simulation(body: TickIn):
    initialise_security_store()
    with connect() as db:
        now = _now()
        rows = db.execute("SELECT * FROM security_units WHERE status!='OFFLINE'").fetchall()
        moved = []
        for row in rows:
            route = _json(row["route_json"], [])
            if len(route) < 2:
                continue
            index = int(row["route_index"] or 0)
            for _ in range(body.steps):
                index = (index + 1) % len(route)
            point = route[index]
            street = point.get("street") or point.get("area") or row["current_street"]
            status = row["status"]
            if point.get("kind") == "INCIDENT" and status in {"RESPONDING", "DISPATCH_PENDING"}:
                status = "ON_SCENE"
            db.execute(
                """
                UPDATE security_units SET latitude=?, longitude=?, current_street=?, route_index=?,
                    status=?, last_tick_at=?, updated_at=? WHERE unit_id=?
                """,
                (
                    float(point["latitude"]), float(point["longitude"]), street, index,
                    status, now, now, row["unit_id"],
                ),
            )
            moved.append({"unit_id": row["unit_id"], "latitude": point["latitude"], "longitude": point["longitude"], "street": street, "status": status})
        # One compact activity entry per simulation step, rather than one database row per car.
        if moved:
            _activity(
                db, "SIMULATION_TICK", "Patrol positions updated",
                f"Moved {len(moved)} units along their assigned street routes.",
                payload={"steps": body.steps, "units": moved},
            )
    return {"moved": moved, "count": len(moved), "simulated_at": _now()}


@router.post("/api/security/dispatch/from-latest-member")
def dispatch_from_latest_member():
    initialise_security_store()
    with connect() as db:
        row = db.execute(
            "SELECT incident_id FROM member_incidents WHERE status='ACTIVE' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active Member incident is available. Confirm one in My Property first.")
        return create_dispatch_for_member_incident(db, row["incident_id"])


@router.post("/api/security/dispatches/{dispatch_id}/acknowledge")
def acknowledge_dispatch(dispatch_id: str, body: AcknowledgeIn):
    initialise_security_store()
    with connect() as db:
        dispatch = _dispatch_payload(db, dispatch_id)
        if not dispatch:
            raise HTTPException(status_code=404, detail="dispatch not found")
        now = _now()
        db.execute(
            "UPDATE security_dispatches SET status='ACKNOWLEDGED', acknowledged_at=? WHERE dispatch_id=?",
            (now, dispatch_id),
        )
        if dispatch.get("selected_unit_id"):
            db.execute(
                "UPDATE security_units SET status='RESPONDING', updated_at=? WHERE unit_id=?",
                (now, dispatch["selected_unit_id"]),
            )
        db.execute(
            "UPDATE security_notifications SET status='ACKNOWLEDGED_IN_APP', delivered_at=COALESCE(delivered_at, ?) WHERE dispatch_id=?",
            (now, dispatch_id),
        )
        _activity(
            db, "DISPATCH_ACKNOWLEDGED", "Control room acknowledged incident",
            f"{dispatch_id} acknowledged by {body.acknowledged_by}.", actor=body.acknowledged_by,
            payload={"dispatch_id": dispatch_id},
        )
        _queue_entity(db, "security_dispatches", dispatch_id, "UPDATE", {"status": "ACKNOWLEDGED", "acknowledged_at": now}, body.acknowledged_by)
        payload = _dispatch_payload(db, dispatch_id)
    return payload


@router.post("/api/security/dispatches/{dispatch_id}/close")
def close_dispatch(dispatch_id: str, body: DispatchCloseIn):
    initialise_security_store()
    with connect() as db:
        return _close_dispatch_in_db(db, dispatch_id, actor=body.closed_by, reason=body.reason)


@router.post("/api/security/notifications/{notification_id}/simulate-send")
def simulate_notification_send(notification_id: str, body: NotificationSendIn):
    initialise_security_store()
    with connect() as db:
        row = db.execute("SELECT * FROM security_notifications WHERE notification_id=?", (notification_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="notification not found")
        now = _now()
        db.execute(
            "UPDATE security_notifications SET status='DELIVERED_DEMO', delivered_at=? WHERE notification_id=?",
            (now, notification_id),
        )
        _activity(
            db, "NOTIFICATION_DELIVERED_DEMO", "WhatsApp-ready alert delivered in demo",
            f"{notification_id} marked delivered by {body.sent_by}; no external message was sent.",
            actor=body.sent_by, payload={"notification_id": notification_id},
        )
        _queue_entity(db, "security_notifications", notification_id, "UPDATE", {"status": "DELIVERED_DEMO", "delivered_at": now}, body.sent_by)
        item = dict(db.execute("SELECT * FROM security_notifications WHERE notification_id=?", (notification_id,)).fetchone())
    return item | {"provider_payload": _json(item.get("provider_payload_json"), {})}


@router.post("/api/security/reset-demo")
def reset_security_demo():
    initialise_security_store()
    with connect() as db:
        db.execute("DELETE FROM security_notifications")
        db.execute("DELETE FROM security_dispatches")
        db.execute("DELETE FROM security_activity")
        now = _now()
        for unit in UNIT_SEED:
            node = ROAD_BY_ID[unit["node_id"]]
            route = [ROAD_BY_ID[item] for item in unit["loop"]]
            db.execute(
                """
                UPDATE security_units SET status='AVAILABLE', latitude=?, longitude=?, current_street=?,
                    route_json=?, route_index=0, route_kind='PATROL_LOOP', active_dispatch_id=NULL,
                    assigned_hotspot_id=NULL, last_tick_at=?, updated_at=? WHERE unit_id=?
                """,
                (node["latitude"], node["longitude"], node["street"], _safe_json(route), now, now, unit["unit_id"]),
            )
        _activity(db, "DEMO_RESET", "Security simulation reset", "Patrol units returned to their seeded Benoni road loops.")
    return {"reset": True, "message": "Security companies, units and hotspots retained; dispatch and notification demo data cleared."}


@router.get("/api/security/database")
def security_database():
    initialise_security_store()
    with connect() as db:
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in SECURITY_TABLES
        }
        writes = [dict(row) for row in db.execute(
            "SELECT * FROM security_activity ORDER BY created_at DESC LIMIT 60"
        ).fetchall()]
        notifications = [dict(row) for row in db.execute(
            "SELECT * FROM security_notifications ORDER BY created_at DESC LIMIT 30"
        ).fetchall()]
        outbox = int(db.execute(
            "SELECT COUNT(*) AS n FROM aws_sync_outbox WHERE status='LOCAL_PENDING'"
        ).fetchone()["n"]) if _table_exists(db, "aws_sync_outbox") else 0
    return {
        "database": str(database_path()),
        "engine": "SQLite WAL",
        "tables": counts,
        "recent_writes": writes,
        "notifications": notifications,
        "aws_outbox_pending": outbox,
        "aws_target_tables": AWS_SECURITY_TABLE_MAP,
    }
