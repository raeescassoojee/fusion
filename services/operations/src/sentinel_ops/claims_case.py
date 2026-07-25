"""Persistent, claim-centred investigation workspace.

The claims workbook remains the immutable historical source. Selecting a claim imports
it into a local case record. From then on every validation, evidence link, plate OCR
observation, agent run, task and report is written to SQLite and copied to the shared
AWS outbox for a later DynamoDB/S3 worker.

The "case agent" is deliberately transparent: it is a deterministic tool-orchestrator
that calls the statistical claims report, incident-window reconstruction, OCR/vision
pipeline and validation store. It recommends next actions; it never makes an automatic
fraud or settlement decision.
"""
from __future__ import annotations

import json
import math
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from sentinel_ops.camera_bridge import camera_ai_to_operations
from sentinel_ops.camera_upload import (
    IMAGE_SUFFIXES,
    MAX_UPLOAD_BYTES,
    UPLOAD_ROOT,
    VIDEO_SUFFIXES,
    _pipeline,
)
from sentinel_ops.claims_bridge import load_claims_hotspots
from sentinel_ops.ingestion import ingest_event
from sentinel_ops.member_mesh import initialise_member_store
from sentinel_ops.models import Claim, Location, ReconstructRequest
from sentinel_ops.rewind import reconstruct_incident
from sentinel_ops.roles_api import METRO_CENTRES, _load_workbook, claim_report, claims
from sentinel_ops.storage import connect, database_path, list_events

router = APIRouter(tags=["claims case workspace"])

CASE_TABLES = (
    "claim_cases",
    "claim_case_tasks",
    "claim_case_activity",
    "claim_case_validations",
    "claim_evidence_links",
    "claim_plate_observations",
    "claim_agent_runs",
    "claim_case_reports",
)
AWS_CASE_TABLE_MAP = {
    "claim_cases": "SentinelClaimCases",
    "claim_case_tasks": "SentinelClaimTasks",
    "claim_case_activity": "SentinelClaimActivity",
    "claim_case_validations": "SentinelClaimValidations",
    "claim_evidence_links": "SentinelClaimEvidence",
    "claim_plate_observations": "SentinelPlateObservations",
    "claim_agent_runs": "SentinelCaseAgentRuns",
    "claim_case_reports": "SentinelClaimReports",
}
VALIDATION_LIBRARY = [
    ("CLAIMANT_IDENTITY", "Claimant identity and contact details"),
    ("POLICY_ACTIVE", "Policy active at incident time"),
    ("INCIDENT_TIME", "Incident date and time sufficiently precise"),
    ("INCIDENT_DESCRIPTION", "Incident description is complete and internally consistent"),
    ("POLICE_REFERENCE", "Police case/reference supplied where required"),
    ("OWNERSHIP", "Ownership / insurable interest verified"),
    ("SUPPORTING_DOCUMENTS", "Required supporting documents received"),
    ("CAMERA_EVIDENCE", "Nearby camera evidence reviewed"),
    ("PLATE_MATCH", "Reported and observed vehicle plates reconciled"),
    ("HUMAN_REVIEW", "Investigator reviewed all AI-assisted findings"),
]
VALIDATION_STATUSES = {"PENDING", "VERIFIED", "MISSING", "MISMATCH", "NOT_APPLICABLE"}
CASE_STATUSES = {"OPEN", "AWAITING_INFORMATION", "UNDER_REVIEW", "READY_FOR_DECISION", "CLOSED"}


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


def _normalise_plate(text: str | None) -> str | None:
    if not text:
        return None
    value = "".join(ch for ch in text.upper() if ch.isalnum())
    return value or None


def _case_id(source_claim_id: str) -> str:
    clean = "".join(ch for ch in source_claim_id.upper() if ch.isalnum())[-18:]
    return f"CASE-{clean or uuid.uuid4().hex[:10].upper()}"


def _activity(
    db,
    case_id: str,
    event_type: str,
    title: str,
    detail: str,
    *,
    actor: str = "Sentinel",
    payload: Any | None = None,
    queue_for_aws: bool = True,
) -> str:
    activity_id = f"ACT-{uuid.uuid4().hex[:12].upper()}"
    created_at = _now()
    payload_json = _safe_json(payload) if payload is not None else None
    db.execute(
        """
        INSERT INTO claim_case_activity(
            activity_id, case_id, event_type, title, detail, payload_json, actor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (activity_id, case_id, event_type, title, detail, payload_json, actor, created_at),
    )
    # Use the existing audit and outbox tables so Member and Claims visibly share one
    # persistence and AWS migration path.
    audit_id = f"AUD-{uuid.uuid4().hex[:12].upper()}"
    db.execute(
        """
        INSERT INTO member_audit_log(
            audit_id, table_name, record_id, action, actor, summary, payload_json, created_at
        ) VALUES (?, 'claim_case_activity', ?, ?, ?, ?, ?, ?)
        """,
        (audit_id, case_id, event_type, actor, title, payload_json, created_at),
    )
    if queue_for_aws:
        db.execute(
            """
            INSERT INTO aws_sync_outbox(
                outbox_id, entity_type, entity_id, action, payload_json, status, created_at
            ) VALUES (?, 'claim_case_activity', ?, ?, ?, 'LOCAL_PENDING', ?)
            """,
            (
                f"OUT-{uuid.uuid4().hex[:12].upper()}",
                activity_id,
                event_type,
                payload_json or _safe_json({"case_id": case_id, "title": title, "detail": detail}),
                created_at,
            ),
        )
    return activity_id


def _queue_entity(db, table: str, entity_id: str, action: str, payload: Any, actor: str) -> None:
    created_at = _now()
    payload_json = _safe_json(payload)
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
        (
            f"OUT-{uuid.uuid4().hex[:12].upper()}", table, entity_id, action,
            payload_json, created_at,
        ),
    )


def initialise_claim_store() -> None:
    initialise_member_store()
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS claim_cases (
                case_id TEXT PRIMARY KEY,
                source_claim_id TEXT NOT NULL UNIQUE,
                member_incident_id TEXT,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                stage TEXT NOT NULL DEFAULT 'TRIAGE',
                priority TEXT NOT NULL DEFAULT 'ROUTINE',
                assigned_to TEXT,
                reported_plate TEXT,
                claim_json TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_claim_cases_status ON claim_cases(status, priority, updated_at DESC);
            CREATE TABLE IF NOT EXISTS claim_case_tasks (
                task_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                priority TEXT NOT NULL DEFAULT 'MEDIUM',
                rationale TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                completed_by TEXT,
                UNIQUE(case_id, title, status),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_tasks_case ON claim_case_tasks(case_id, status, priority);
            CREATE TABLE IF NOT EXISTS claim_case_activity (
                activity_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                payload_json TEXT,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_activity_case ON claim_case_activity(case_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS claim_case_validations (
                case_id TEXT NOT NULL,
                check_code TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                value TEXT,
                note TEXT,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(case_id, check_code),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE TABLE IF NOT EXISTS claim_evidence_links (
                link_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL,
                summary TEXT NOT NULL,
                media_url TEXT,
                payload_json TEXT,
                linked_at TEXT NOT NULL,
                linked_by TEXT NOT NULL,
                UNIQUE(case_id, evidence_type, evidence_id),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_case ON claim_evidence_links(case_id, linked_at DESC);
            CREATE TABLE IF NOT EXISTS claim_plate_observations (
                observation_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                event_id TEXT,
                plate_text TEXT,
                normalized_plate TEXT,
                ocr_confidence REAL,
                detection_confidence REAL,
                camera_id TEXT,
                captured_at TEXT,
                media_url TEXT,
                match_status TEXT NOT NULL DEFAULT 'UNASSESSED',
                payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_plates_case ON claim_plate_observations(case_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_claim_plates_value ON claim_plate_observations(normalized_plate);
            CREATE TABLE IF NOT EXISTS claim_agent_runs (
                run_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                status TEXT NOT NULL,
                readiness_score REAL NOT NULL,
                recommendation TEXT NOT NULL,
                rationale_json TEXT NOT NULL,
                tools_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_agent_case ON claim_agent_runs(case_id, completed_at DESC);
            CREATE TABLE IF NOT EXISTS claim_case_reports (
                report_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                generated_by TEXT NOT NULL,
                UNIQUE(case_id, version),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            """
        )


def _claim_location(claim: dict[str, Any]) -> tuple[float, float]:
    if claim.get("latitude") is not None and claim.get("longitude") is not None:
        return float(claim["latitude"]), float(claim["longitude"])
    suburb = (claim.get("suburb") or "").strip().lower()
    try:
        hotspots, _ = load_claims_hotspots()
        for hotspot in hotspots:
            if hotspot.name.strip().lower() == suburb:
                return hotspot.location.latitude, hotspot.location.longitude
    except Exception:
        pass
    # The workbook does not carry exact coordinates. This fallback is explicit in
    # the response and is used only to make a retrieval window possible.
    return METRO_CENTRES.get("Gauteng", (-26.1, 28.05))


def _get_case(case_id: str) -> dict[str, Any]:
    initialise_claim_store()
    with connect() as db:
        row = _row(db.execute("SELECT * FROM claim_cases WHERE case_id=?", (case_id,)).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail="case not found")
    row["claim"] = _json(row.pop("claim_json"), {})
    return row


def _ensure_validations(db, case_id: str, claim: dict[str, Any]) -> None:
    now = _now()
    item_type = (claim.get("item_type") or "").lower()
    for code, label in VALIDATION_LIBRARY:
        status = "PENDING"
        note = None
        if code == "PLATE_MATCH" and item_type != "vehicle":
            status, note = "NOT_APPLICABLE", "Non-vehicle claim"
        db.execute(
            """
            INSERT INTO claim_case_validations(
                case_id, check_code, label, status, value, note, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, NULL, ?, 'Sentinel', ?)
            ON CONFLICT(case_id, check_code) DO NOTHING
            """,
            (case_id, code, label, status, note, now),
        )


def _create_case(claim: dict[str, Any], source_type: str, member_incident_id: str | None = None) -> dict[str, Any]:
    initialise_claim_store()
    source_claim_id = str(claim["incident_id"])
    case_id = _case_id(source_claim_id)
    now = _now()
    with connect() as db:
        existing = _row(db.execute(
            "SELECT * FROM claim_cases WHERE source_claim_id=?", (source_claim_id,)
        ).fetchone())
        if existing:
            return _get_case(existing["case_id"])
        db.execute(
            """
            INSERT INTO claim_cases(
                case_id, source_claim_id, member_incident_id, source_type, status, stage,
                priority, claim_json, opened_at, updated_at
            ) VALUES (?, ?, ?, ?, 'OPEN', 'TRIAGE', 'ROUTINE', ?, ?, ?)
            """,
            (case_id, source_claim_id, member_incident_id, source_type, _safe_json(claim), now, now),
        )
        _ensure_validations(db, case_id, claim)
        _activity(
            db, case_id, "CASE_OPENED", "Case workspace opened",
            f"{source_claim_id} imported from {source_type.replace('_', ' ').lower()} into the investigation database.",
            actor="Claims operator", payload={"claim": claim, "source_type": source_type},
        )
        _queue_entity(db, "claim_cases", case_id, "INSERT", {
            "case_id": case_id, "source_claim_id": source_claim_id,
            "source_type": source_type, "claim": claim,
        }, "Claims operator")
    return _get_case(case_id)


def _case_payload(case_id: str) -> dict[str, Any]:
    case = _get_case(case_id)
    with connect() as db:
        tasks = [dict(row) for row in db.execute(
            "SELECT * FROM claim_case_tasks WHERE case_id=? ORDER BY status, priority DESC, created_at DESC",
            (case_id,),
        ).fetchall()]
        validations = [dict(row) for row in db.execute(
            "SELECT * FROM claim_case_validations WHERE case_id=? ORDER BY rowid", (case_id,)
        ).fetchall()]
        evidence = [dict(row) for row in db.execute(
            "SELECT * FROM claim_evidence_links WHERE case_id=? ORDER BY linked_at DESC", (case_id,)
        ).fetchall()]
        plates = [dict(row) for row in db.execute(
            "SELECT * FROM claim_plate_observations WHERE case_id=? ORDER BY created_at DESC", (case_id,)
        ).fetchall()]
        activity = [dict(row) for row in db.execute(
            "SELECT * FROM claim_case_activity WHERE case_id=? ORDER BY created_at DESC LIMIT 200", (case_id,)
        ).fetchall()]
        agent = _row(db.execute(
            "SELECT * FROM claim_agent_runs WHERE case_id=? ORDER BY completed_at DESC LIMIT 1", (case_id,)
        ).fetchone())
        report = _row(db.execute(
            "SELECT * FROM claim_case_reports WHERE case_id=? ORDER BY version DESC LIMIT 1", (case_id,)
        ).fetchone())
    for collection in (evidence, plates, activity):
        for item in collection:
            if "payload_json" in item:
                item["payload"] = _json(item.pop("payload_json"), {})
    if agent:
        agent["rationale"] = _json(agent.pop("rationale_json"), [])
        agent["tools"] = _json(agent.pop("tools_json"), [])
    if report:
        report["report"] = _json(report.pop("report_json"), {})
    try:
        statistical = claim_report(case["source_claim_id"])
    except Exception:
        statistical = _local_claim_context(case["claim"])
    return {
        "case": case,
        "tasks": tasks,
        "validations": validations,
        "evidence": evidence,
        "plates": plates,
        "activity": activity,
        "agent": agent,
        "latest_report": report,
        "statistical_report": statistical,
        "database": _database_summary(case_id),
        "notice": "AI and rules assist triage; a human investigator remains responsible for every decision.",
    }


def _local_claim_context(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim": claim,
        "context": {"suburb_incidents_5y": 0, "suburb_median": 0, "amount_percentile": None},
        "coverage": {"cameras": []},
        "cluster": [],
        "findings": [{
            "level": "INFO", "code": "LIVE_MEMBER_CASE",
            "title": "Case originated from a live Member incident",
            "detail": "The case can use the linked doorbell sightings and incident trail even when it is not present in the historical workbook.",
        }],
        "assessment": {"priority": "MEDIUM", "recommendation": "Review linked live evidence.", "flags": 0, "watches": 0},
        "disclaimer": "This live demonstration case is not part of the supplied historical workbook.",
    }


def _database_summary(case_id: str) -> dict[str, Any]:
    path = database_path()
    with connect() as db:
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE case_id=?", (case_id,)).fetchone()["n"])
            for table in CASE_TABLES if table != "claim_cases"
        }
        counts["claim_cases"] = int(db.execute(
            "SELECT COUNT(*) AS n FROM claim_cases WHERE case_id=?", (case_id,)
        ).fetchone()["n"])
        pending = int(db.execute(
            "SELECT COUNT(*) AS n FROM aws_sync_outbox WHERE status='LOCAL_PENDING' AND "
            "(entity_id=? OR payload_json LIKE ?)", (case_id, f"%{case_id}%")
        ).fetchone()["n"])
        recent = [dict(row) for row in db.execute(
            "SELECT table_name, record_id, action, summary, actor, created_at FROM member_audit_log "
            "WHERE record_id=? OR payload_json LIKE ? ORDER BY created_at DESC LIMIT 20",
            (case_id, f"%{case_id}%"),
        ).fetchall()]
    return {
        "engine": "SQLite WAL",
        "database_path": str(path),
        "database_size_bytes": path.stat().st_size if path.exists() else 0,
        "counts": counts,
        "pending_aws_records": pending,
        "recent_writes": recent,
        "aws_targets": AWS_CASE_TABLE_MAP,
    }


class MemberClaimCreate(BaseModel):
    claim_amount: float = Field(default=185_000, ge=0)
    claim_type: str = "Home Invasion"
    item_type: str = "Contents"
    assigned_to: str = "Demo investigator"


class ValidationUpdate(BaseModel):
    check_code: str
    status: Literal["PENDING", "VERIFIED", "MISSING", "MISMATCH", "NOT_APPLICABLE"]
    value: str | None = None
    note: str = ""
    updated_by: str = "Demo investigator"


class CaseDetailsUpdate(BaseModel):
    assigned_to: str | None = None
    reported_plate: str | None = None
    status: Literal["OPEN", "AWAITING_INFORMATION", "UNDER_REVIEW", "READY_FOR_DECISION", "CLOSED"] | None = None
    stage: str | None = None
    updated_by: str = "Demo investigator"


class TaskComplete(BaseModel):
    completed_by: str = "Demo investigator"


class DecisionUpdate(BaseModel):
    decision: Literal["REQUEST_INFORMATION", "REFER_SPECIALIST", "READY_FOR_SETTLEMENT", "CLOSE_NO_ACTION"]
    rationale: str = Field(..., min_length=5)
    updated_by: str = "Demo investigator"


@router.get("/api/fraud/cases/queue")
def case_queue(
    q: str | None = None,
    peril: str | None = None,
    item_type: str | None = None,
    min_amount: float | None = None,
    sort: Literal["recent", "amount", "suburb", "priority"] = "recent",
    limit: int = Query(120, ge=1, le=500),
):
    initialise_claim_store()
    historical = list(claims())
    with connect() as db:
        case_rows = [dict(row) for row in db.execute("SELECT * FROM claim_cases").fetchall()]
    by_source = {row["source_claim_id"]: row for row in case_rows}
    historical_ids = {str(row["incident_id"]) for row in historical}
    rows: list[dict[str, Any]] = []
    for claim in historical:
        case = by_source.get(str(claim["incident_id"]))
        rows.append({
            **claim,
            "case_id": case["case_id"] if case else None,
            "case_status": case["status"] if case else "NOT_OPENED",
            "case_stage": case["stage"] if case else "QUEUE",
            "case_priority": case["priority"] if case else "UNASSESSED",
            "assigned_to": case["assigned_to"] if case else None,
            "source_type": "WORKBOOK",
        })
    for case in case_rows:
        if case["source_claim_id"] in historical_ids:
            continue
        claim = _json(case["claim_json"], {})
        rows.append({
            **claim,
            "case_id": case["case_id"],
            "case_status": case["status"],
            "case_stage": case["stage"],
            "case_priority": case["priority"],
            "assigned_to": case["assigned_to"],
            "source_type": case["source_type"],
        })

    def keep(c: dict[str, Any]) -> bool:
        if peril and (c.get("peril") or "").lower() != peril.lower():
            return False
        if item_type and (c.get("item_type") or "").lower() != item_type.lower():
            return False
        if min_amount is not None and float(c.get("amount") or 0) < min_amount:
            return False
        if q:
            hay = " ".join(str(c.get(k) or "") for k in (
                "incident_id", "suburb", "peril", "vehicle_make", "vehicle_model",
                "case_status", "case_priority", "assigned_to",
            ))
            if q.lower() not in hay.lower():
                return False
        return True

    filtered = [c for c in rows if keep(c)]
    priority_rank = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "ROUTINE": 1, "UNASSESSED": 0}
    if sort == "amount":
        filtered.sort(key=lambda c: float(c.get("amount") or 0), reverse=True)
    elif sort == "suburb":
        filtered.sort(key=lambda c: ((c.get("suburb") or ""), -float(c.get("amount") or 0)))
    elif sort == "priority":
        filtered.sort(key=lambda c: (priority_rank.get(c.get("case_priority"), 0), c.get("incident_at") or ""), reverse=True)
    else:
        filtered.sort(key=lambda c: c.get("incident_at") or "", reverse=True)
    return {
        "total": len(filtered),
        "value": round(sum(float(c.get("amount") or 0) for c in filtered), 2),
        "claims": filtered[:limit],
        "facets": {
            "perils": dict(Counter(c.get("peril") for c in filtered if c.get("peril")).most_common(12)),
            "item_types": dict(Counter(c.get("item_type") for c in filtered if c.get("item_type")).most_common()),
            "statuses": dict(Counter(c.get("case_status") for c in filtered).most_common()),
        },
        "workbook_source": _load_workbook()[1],
    }


@router.post("/api/fraud/cases/open/{source_claim_id}")
def open_case(source_claim_id: str):
    claim = next((row for row in claims() if str(row["incident_id"]) == source_claim_id), None)
    if not claim:
        with connect() as db:
            existing = db.execute(
                "SELECT case_id FROM claim_cases WHERE source_claim_id=?", (source_claim_id,)
            ).fetchone()
        if existing:
            return _case_payload(existing["case_id"])
        raise HTTPException(status_code=404, detail="claim not found in workbook or local case database")
    case = _create_case(claim, "WORKBOOK")
    return _case_payload(case["case_id"])


@router.post("/api/fraud/cases/from-member/latest")
def create_case_from_latest_member_incident(body: MemberClaimCreate):
    initialise_claim_store()
    with connect() as db:
        incident = _row(db.execute(
            "SELECT * FROM member_incidents ORDER BY started_at DESC LIMIT 1"
        ).fetchone())
        if not incident:
            raise HTTPException(status_code=404, detail="no Member incident exists yet")
        origin = _row(db.execute(
            """
            SELECT u.household, u.suburb, u.metro, u.latitude, u.longitude,
                   s.captured_at, s.profile_id
            FROM member_users u
            JOIN face_sightings s ON s.user_id=u.user_id
            WHERE s.sighting_id=?
            """,
            (incident["origin_sighting_id"],),
        ).fetchone())
    if not origin:
        raise HTTPException(status_code=422, detail="Member incident is missing its origin sighting")
    claim_id = f"CLM-LIVE-{incident['incident_id'].split('-')[-1]}"
    claim = {
        "incident_id": claim_id,
        "peril": body.claim_type,
        "suburb": origin["suburb"],
        "household": origin["household"],
        "metro": origin["metro"],
        "latitude": origin["latitude"],
        "longitude": origin["longitude"],
        "item_type": body.item_type,
        "item_category": body.item_type,
        "peril_descr": incident["notes"] or body.claim_type,
        "vehicle_make": None,
        "vehicle_model": None,
        "vehicle_year": None,
        "incident_at": incident["started_at"],
        "amount": body.claim_amount,
    }
    case = _create_case(claim, "MEMBER_INCIDENT", incident["incident_id"])
    with connect() as db:
        db.execute(
            "UPDATE claim_cases SET assigned_to=?, updated_at=? WHERE case_id=?",
            (body.assigned_to, _now(), case["case_id"]),
        )
        _link_member_incident(db, case["case_id"], incident["incident_id"])
    return _case_payload(case["case_id"])


def _link_member_incident(db, case_id: str, incident_id: str) -> None:
    incident = _row(db.execute("SELECT * FROM member_incidents WHERE incident_id=?", (incident_id,)).fetchone())
    if not incident:
        return
    sightings = [dict(row) for row in db.execute(
        """
        SELECT s.*, u.household, u.suburb, c.device_label
        FROM face_sightings s
        JOIN member_users u ON u.user_id=s.user_id
        JOIN member_cameras c ON c.camera_id=s.camera_id
        WHERE s.profile_id=? AND s.captured_at>=?
        ORDER BY s.captured_at
        """,
        (incident["profile_id"], incident["started_at"]),
    ).fetchall()]
    link_id = f"EVD-{uuid.uuid4().hex[:12].upper()}"
    db.execute(
        """
        INSERT INTO claim_evidence_links(
            link_id, case_id, evidence_type, evidence_id, source, status, confidence,
            summary, media_url, payload_json, linked_at, linked_by
        ) VALUES (?, ?, 'MEMBER_INCIDENT', ?, 'MEMBER_MESH', 'LINKED', 100,
                  ?, NULL, ?, ?, 'Sentinel')
        ON CONFLICT(case_id, evidence_type, evidence_id) DO UPDATE SET
            payload_json=excluded.payload_json, linked_at=excluded.linked_at
        """,
        (
            link_id, case_id, incident_id,
            f"Confirmed Member incident with {len(sightings)} linked camera sighting(s)",
            _safe_json({"incident": incident, "sightings": sightings}), _now(),
        ),
    )
    _activity(
        db, case_id, "MEMBER_INCIDENT_LINKED", "Member incident linked",
        f"{len(sightings)} face sighting(s) and the neighbourhood watch trail are now available to the claim.",
        payload={"incident_id": incident_id, "sightings": len(sightings)},
    )
    _queue_entity(db, "claim_evidence_links", link_id, "UPSERT", {
        "case_id": case_id, "incident_id": incident_id, "sightings": sightings,
    }, "Sentinel")


@router.get("/api/fraud/cases/{case_id}")
def get_case_workspace(case_id: str):
    return _case_payload(case_id)


@router.patch("/api/fraud/cases/{case_id}")
def update_case_details(case_id: str, body: CaseDetailsUpdate):
    case = _get_case(case_id)
    changes: list[str] = []
    values: list[Any] = []
    for name in ("assigned_to", "reported_plate", "status", "stage"):
        value = getattr(body, name)
        if value is not None:
            changes.append(f"{name}=?")
            values.append(value.strip() if isinstance(value, str) else value)
    if not changes:
        return _case_payload(case_id)
    changes.append("updated_at=?")
    values.extend([_now(), case_id])
    with connect() as db:
        db.execute(f"UPDATE claim_cases SET {', '.join(changes)} WHERE case_id=?", values)
        _activity(
            db, case_id, "CASE_UPDATED", "Case details updated",
            ", ".join(name.split("=")[0] for name in changes[:-1]),
            actor=body.updated_by, payload=body.model_dump(exclude_none=True),
        )
        _queue_entity(db, "claim_cases", case_id, "UPDATE", body.model_dump(exclude_none=True), body.updated_by)
    return _case_payload(case_id)


@router.post("/api/fraud/cases/{case_id}/validations")
def update_validation(case_id: str, body: ValidationUpdate):
    _get_case(case_id)
    if body.status not in VALIDATION_STATUSES:
        raise HTTPException(status_code=422, detail="unsupported validation status")
    now = _now()
    with connect() as db:
        current = _row(db.execute(
            "SELECT label FROM claim_case_validations WHERE case_id=? AND check_code=?",
            (case_id, body.check_code),
        ).fetchone())
        if not current:
            raise HTTPException(status_code=404, detail="validation check not found")
        db.execute(
            """
            UPDATE claim_case_validations
            SET status=?, value=?, note=?, updated_by=?, updated_at=?
            WHERE case_id=? AND check_code=?
            """,
            (body.status, body.value, body.note, body.updated_by, now, case_id, body.check_code),
        )
        _activity(
            db, case_id, "VALIDATION_UPDATED", current["label"],
            f"{body.status.replace('_', ' ')}{': ' + body.note if body.note else ''}",
            actor=body.updated_by, payload=body.model_dump(),
        )
        _queue_entity(db, "claim_case_validations", f"{case_id}:{body.check_code}", "UPSERT", body.model_dump(), body.updated_by)
    return _case_payload(case_id)


@router.post("/api/fraud/cases/{case_id}/tasks/{task_id}/complete")
def complete_task(case_id: str, task_id: str, body: TaskComplete):
    _get_case(case_id)
    with connect() as db:
        task = _row(db.execute(
            "SELECT * FROM claim_case_tasks WHERE case_id=? AND task_id=?", (case_id, task_id)
        ).fetchone())
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        db.execute(
            "UPDATE claim_case_tasks SET status='DONE', completed_at=?, completed_by=? WHERE task_id=?",
            (_now(), body.completed_by, task_id),
        )
        _activity(db, case_id, "TASK_COMPLETED", task["title"], "Marked complete.", actor=body.completed_by)
        _queue_entity(db, "claim_case_tasks", task_id, "UPDATE", {"status": "DONE"}, body.completed_by)
    return _case_payload(case_id)


def _create_task(db, case_id: str, title: str, category: str, priority: str, rationale: str) -> None:
    exists = db.execute(
        "SELECT 1 FROM claim_case_tasks WHERE case_id=? AND title=? AND status='OPEN'",
        (case_id, title),
    ).fetchone()
    if exists:
        return
    task_id = f"TSK-{uuid.uuid4().hex[:10].upper()}"
    now = _now()
    db.execute(
        """
        INSERT INTO claim_case_tasks(
            task_id, case_id, title, category, status, priority, rationale, created_by, created_at
        ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, 'Case Agent', ?)
        """,
        (task_id, case_id, title, category, priority, rationale, now),
    )
    _queue_entity(db, "claim_case_tasks", task_id, "INSERT", {
        "case_id": case_id, "title": title, "category": category,
        "priority": priority, "rationale": rationale,
    }, "Case Agent")


def _refresh_evidence(case_id: str, actor: str = "Case Agent") -> dict[str, Any]:
    case = _get_case(case_id)
    claim = case["claim"]
    lat, lon = _claim_location(claim)
    incident_at = claim.get("incident_at")
    if not incident_at:
        return {"events": 0, "member_incident": False, "message": "incident time unavailable"}
    try:
        dt = datetime.fromisoformat(incident_at)
    except ValueError:
        return {"events": 0, "member_incident": False, "message": "invalid incident time"}
    model_claim = Claim(
        claim_id=case["source_claim_id"],
        incident_time=dt,
        location=Location(latitude=lat, longitude=lon),
        claim_type=claim.get("peril") or "Unknown",
        claim_amount=float(claim.get("amount") or 0),
        plate_text=case.get("reported_plate"),
        vehicle_colour=None,
        vehicle_type=claim.get("item_type"),
    )
    timeline = reconstruct_incident(ReconstructRequest(
        claim=model_claim, events=list_events(limit=1000), radius_km=8,
        minutes_before=180, minutes_after=180,
    ))
    linked = 0
    with connect() as db:
        for item in timeline.items:
            link_id = f"EVD-{uuid.uuid4().hex[:12].upper()}"
            payload = item.model_dump(mode="json")
            db.execute(
                """
                INSERT INTO claim_evidence_links(
                    link_id, case_id, evidence_type, evidence_id, source, status, confidence,
                    summary, media_url, payload_json, linked_at, linked_by
                ) VALUES (?, ?, 'CAMERA_EVENT', ?, 'OPERATIONS_EVENTS', 'CANDIDATE', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id, evidence_type, evidence_id) DO UPDATE SET
                    confidence=excluded.confidence, summary=excluded.summary,
                    media_url=excluded.media_url, payload_json=excluded.payload_json,
                    linked_at=excluded.linked_at
                """,
                (
                    link_id, case_id, item.event_id, item.relevance_score,
                    item.description, item.media_url, _safe_json(payload), _now(), actor,
                ),
            )
            linked += 1
        member_linked = False
        if case.get("member_incident_id"):
            _link_member_incident(db, case_id, case["member_incident_id"])
            member_linked = True
        _activity(
            db, case_id, "EVIDENCE_REFRESHED", "Evidence search completed",
            f"{linked} camera event(s) fell inside the time, distance and relevance window.",
            actor=actor, payload={"timeline": timeline.model_dump(mode="json")},
        )
    return {"events": linked, "member_incident": member_linked, "timeline": timeline.model_dump(mode="json")}


@router.post("/api/fraud/cases/{case_id}/evidence/refresh")
def refresh_case_evidence(case_id: str):
    _refresh_evidence(case_id, actor="Demo investigator")
    return _case_payload(case_id)


@router.post("/api/fraud/cases/{case_id}/plate-scan")
async def case_plate_scan(
    case_id: str,
    file: UploadFile = File(...),
    camera_id: str = Form("CLAIMS-DESK-UPLOAD"),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    captured_at: str | None = Form(None),
):
    case = _get_case(case_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="upload an image or video supported by the camera pipeline")
    batch = uuid.uuid4().hex[:10]
    out_dir = UPLOAD_ROOT / f"claim-{case_id.lower()}-{batch}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.gettempdir()) / f"sentinel-claim-{batch}{suffix}"
    with tmp.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    if tmp.stat().st_size > MAX_UPLOAD_BYTES:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="file exceeds the configured demo upload limit")
    claim_lat, claim_lon = _claim_location(case["claim"])
    lat = latitude if latitude is not None else claim_lat
    lon = longitude if longitude is not None else claim_lon
    try:
        pipeline = _pipeline(out_dir)
        start = datetime.fromisoformat(captured_at) if captured_at else datetime.now().astimezone()
        results = pipeline.process_media(
            input_path=tmp, camera_id=camera_id, latitude=lat, longitude=lon,
            mode="HEIGHTENED", start_timestamp=start,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"plate/vision pipeline failed: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)

    observations: list[dict[str, Any]] = []
    reported = _normalise_plate(case.get("reported_plate"))
    with connect() as db:
        for _, event_path in results:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            payload["source_media"] = file.filename
            payload["_batch"] = batch
            media_base = f"/api/cameras/media/{out_dir.name}"
            if payload.get("media_url"):
                payload["media_url"] = f"{media_base}/{str(payload['media_url']).lstrip('/')}"
            try:
                ingest_event(camera_ai_to_operations(payload))
            except Exception:
                # The claim-specific evidence record is still useful even if the
                # generic operations event store rejects a partial detector result.
                pass
            plate = payload.get("plate") or {}
            text = plate.get("text") or plate.get("display_text")
            normal = _normalise_plate(text)
            if reported and normal:
                match_status = "MATCH" if normal == reported else "MISMATCH"
            elif normal:
                match_status = "CANDIDATE"
            else:
                match_status = "NO_READ"
            obs_id = f"PLT-{uuid.uuid4().hex[:12].upper()}"
            media_url = plate.get("crop_url") or payload.get("media_url")
            if media_url and not str(media_url).startswith(("/", "http://", "https://")):
                media_url = f"{media_base}/{str(media_url).lstrip('/')}"
            db.execute(
                """
                INSERT INTO claim_plate_observations(
                    observation_id, case_id, event_id, plate_text, normalized_plate,
                    ocr_confidence, detection_confidence, camera_id, captured_at,
                    media_url, match_status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obs_id, case_id, payload.get("event_id"), text, normal,
                    plate.get("ocr_confidence"), plate.get("detection_confidence"),
                    payload.get("camera_id") or camera_id, payload.get("timestamp"),
                    media_url, match_status, _safe_json(payload), _now(),
                ),
            )
            link_id = f"EVD-{uuid.uuid4().hex[:12].upper()}"
            summary = (
                f"Plate {text or 'unreadable'} · {match_status.replace('_', ' ').lower()} · "
                f"OCR {round(float(plate.get('ocr_confidence') or 0) * 100)}%"
            )
            db.execute(
                """
                INSERT INTO claim_evidence_links(
                    link_id, case_id, evidence_type, evidence_id, source, status, confidence,
                    summary, media_url, payload_json, linked_at, linked_by
                ) VALUES (?, ?, 'PLATE_OBSERVATION', ?, 'CAMERA_AI', ?, ?, ?, ?, ?, ?, 'Demo investigator')
                """,
                (
                    link_id, case_id, obs_id, match_status,
                    float(plate.get("ocr_confidence") or 0) * 100,
                    summary, media_url, _safe_json(payload), _now(),
                ),
            )
            _queue_entity(db, "claim_plate_observations", obs_id, "INSERT", {
                "case_id": case_id, "plate": text, "normalised": normal,
                "match_status": match_status, "event": payload,
            }, "Camera AI")
            observations.append({
                "observation_id": obs_id, "plate_text": text, "normalized_plate": normal,
                "match_status": match_status, "ocr_confidence": plate.get("ocr_confidence"),
                "detection_confidence": plate.get("detection_confidence"), "media_url": media_url,
            })
        _activity(
            db, case_id, "PLATE_SCAN_COMPLETED", "Number-plate analysis completed",
            f"{len(observations)} candidate event(s) processed from {file.filename}.",
            actor="Camera AI", payload={"observations": observations, "batch": batch},
        )
    return {"case_id": case_id, "batch": batch, "observations": observations, "workspace": _case_payload(case_id)}


@router.post("/api/fraud/cases/{case_id}/agent/run")
def run_case_agent(case_id: str):
    case = _get_case(case_id)
    started = _now()
    tools: list[dict[str, Any]] = []
    rationale: list[dict[str, Any]] = []

    # Tool 1: traceable claims analytics.
    try:
        stat = claim_report(case["source_claim_id"])
        tools.append({"tool": "claims_context", "status": "OK", "findings": len(stat["findings"])})
    except Exception:
        stat = _local_claim_context(case["claim"])
        tools.append({"tool": "claims_context", "status": "LOCAL_CASE", "findings": len(stat["findings"])})
    flags = int(stat["assessment"].get("flags") or 0)
    watches = int(stat["assessment"].get("watches") or 0)
    if flags:
        rationale.append({"level": "ATTENTION", "reason": f"{flags} statistical flag(s) require human review."})

    # Tool 2: evidence retrieval.
    evidence_result = _refresh_evidence(case_id)
    tools.append({"tool": "incident_window_retrieval", "status": "OK", **{k: v for k, v in evidence_result.items() if k != "timeline"}})

    with connect() as db:
        validations = [dict(row) for row in db.execute(
            "SELECT * FROM claim_case_validations WHERE case_id=?", (case_id,)
        ).fetchall()]
        plates = [dict(row) for row in db.execute(
            "SELECT * FROM claim_plate_observations WHERE case_id=?", (case_id,)
        ).fetchall()]
        evidence_count = int(db.execute(
            "SELECT COUNT(*) AS n FROM claim_evidence_links WHERE case_id=?", (case_id,)
        ).fetchone()["n"])

        pending = [v for v in validations if v["status"] == "PENDING"]
        missing = [v for v in validations if v["status"] == "MISSING"]
        mismatches = [v for v in validations if v["status"] == "MISMATCH"]
        verified = [v for v in validations if v["status"] in {"VERIFIED", "NOT_APPLICABLE"}]
        plate_mismatch = any(p["match_status"] == "MISMATCH" for p in plates)
        plate_match = any(p["match_status"] == "MATCH" for p in plates)

        if missing:
            rationale.append({"level": "BLOCKER", "reason": f"{len(missing)} required validation(s) are marked missing."})
            _create_task(db, case_id, "Request missing claim information", "DOCUMENTS", "HIGH", "One or more validation checks are marked MISSING.")
        if pending:
            _create_task(db, case_id, "Complete outstanding validation checklist", "VALIDATION", "MEDIUM", f"{len(pending)} checks are still pending.")
        if flags or mismatches or plate_mismatch:
            _create_task(db, case_id, "Second-reader investigator review", "REVIEW", "HIGH", "Statistical or evidence inconsistencies require a human second reader.")
        if evidence_count == 0:
            _create_task(db, case_id, "Retrieve camera evidence for incident window", "EVIDENCE", "HIGH", "No evidence is linked to the case yet.")
        if (case["claim"].get("item_type") or "").lower() == "vehicle" and not plates:
            _create_task(db, case_id, "Run number-plate OCR on submitted footage", "PLATE", "MEDIUM", "Vehicle claim has no plate observations.")
        if plates and not case.get("reported_plate"):
            _create_task(db, case_id, "Capture the reported vehicle registration", "PLATE", "HIGH", "OCR observations exist but the reported plate field is blank.")
        if plate_match:
            rationale.append({"level": "SUPPORT", "reason": "At least one OCR observation matches the reported plate."})
        if evidence_count:
            rationale.append({"level": "SUPPORT", "reason": f"{evidence_count} evidence item(s) are linked and auditable."})

        applicable = max(1, len([v for v in validations if v["status"] != "NOT_APPLICABLE"]))
        readiness = 20 + 55 * len([v for v in validations if v["status"] == "VERIFIED"]) / applicable
        readiness += min(15, evidence_count * 3)
        readiness += 10 if plate_match else 0
        readiness -= 15 * len(missing)
        readiness -= 18 * len(mismatches)
        readiness -= 12 if plate_mismatch else 0
        readiness -= min(20, flags * 8 + watches * 2)
        readiness = round(max(0, min(100, readiness)), 1)

        if missing:
            recommendation, status, stage, priority = "REQUEST_INFORMATION", "AWAITING_INFORMATION", "INFORMATION", "HIGH"
        elif mismatches or plate_mismatch or flags >= 2:
            recommendation, status, stage, priority = "INVESTIGATOR_REVIEW", "UNDER_REVIEW", "INVESTIGATION", "HIGH"
        elif pending or flags or watches:
            recommendation, status, stage, priority = "COMPLETE_REVIEW", "UNDER_REVIEW", "VALIDATION", "MEDIUM"
        elif readiness >= 75:
            recommendation, status, stage, priority = "READY_FOR_HUMAN_DECISION", "READY_FOR_DECISION", "DECISION", "LOW"
        else:
            recommendation, status, stage, priority = "GATHER_EVIDENCE", "UNDER_REVIEW", "EVIDENCE", "MEDIUM"

        run_id = f"AGT-{uuid.uuid4().hex[:12].upper()}"
        completed = _now()
        db.execute(
            """
            INSERT INTO claim_agent_runs(
                run_id, case_id, status, readiness_score, recommendation,
                rationale_json, tools_json, started_at, completed_at
            ) VALUES (?, ?, 'COMPLETED', ?, ?, ?, ?, ?, ?)
            """,
            (run_id, case_id, readiness, recommendation, _safe_json(rationale), _safe_json(tools), started, completed),
        )
        db.execute(
            "UPDATE claim_cases SET status=?, stage=?, priority=?, updated_at=? WHERE case_id=?",
            (status, stage, priority, completed, case_id),
        )
        _activity(
            db, case_id, "AGENT_COMPLETED", "Sentinel Case Agent completed",
            f"Readiness {readiness:.0f}/100 · recommendation {recommendation.replace('_', ' ').lower()}.",
            actor="Case Agent", payload={"run_id": run_id, "rationale": rationale, "tools": tools},
        )
        _queue_entity(db, "claim_agent_runs", run_id, "INSERT", {
            "case_id": case_id, "readiness_score": readiness,
            "recommendation": recommendation, "rationale": rationale, "tools": tools,
        }, "Case Agent")
        _queue_entity(db, "claim_cases", case_id, "UPDATE", {
            "status": status, "stage": stage, "priority": priority,
        }, "Case Agent")
    return _case_payload(case_id)


@router.post("/api/fraud/cases/{case_id}/decision")
def record_case_decision(case_id: str, body: DecisionUpdate):
    _get_case(case_id)
    status = "CLOSED" if body.decision in {"READY_FOR_SETTLEMENT", "CLOSE_NO_ACTION"} else "UNDER_REVIEW"
    stage = "CLOSED" if status == "CLOSED" else "ESCALATED"
    now = _now()
    with connect() as db:
        db.execute(
            "UPDATE claim_cases SET status=?, stage=?, updated_at=?, closed_at=? WHERE case_id=?",
            (status, stage, now, now if status == "CLOSED" else None, case_id),
        )
        _activity(
            db, case_id, "HUMAN_DECISION", body.decision.replace("_", " ").title(),
            body.rationale, actor=body.updated_by,
            payload=body.model_dump(),
        )
        _queue_entity(db, "claim_cases", case_id, "DECISION", body.model_dump(), body.updated_by)
    return _case_payload(case_id)


@router.post("/api/fraud/cases/{case_id}/report/generate")
def generate_case_report(case_id: str, generated_by: str = "Demo investigator"):
    workspace = _case_payload(case_id)
    case = workspace["case"]
    validations = workspace["validations"]
    evidence = workspace["evidence"]
    plates = workspace["plates"]
    agent = workspace["agent"]
    report = {
        "report_type": "SENTINEL_CLAIM_INVESTIGATION_PACK",
        "case_id": case_id,
        "source_claim_id": case["source_claim_id"],
        "generated_at": _now(),
        "claim": case["claim"],
        "case_state": {
            "status": case["status"], "stage": case["stage"],
            "priority": case["priority"], "assigned_to": case.get("assigned_to"),
        },
        "statistical_context": workspace["statistical_report"],
        "validations": validations,
        "evidence": evidence,
        "plate_observations": plates,
        "agent_assistance": agent,
        "activity": list(reversed(workspace["activity"])),
        "governance": {
            "decision_owner": "Human claims investigator",
            "limitations": [
                "Face and plate matches are candidate evidence, not identity or guilt determinations.",
                "Historical statistical flags indicate review priority, not fraud.",
                "Any settlement, repudiation or referral decision must be made and recorded by an authorised human.",
            ],
        },
    }
    with connect() as db:
        version = int(db.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS v FROM claim_case_reports WHERE case_id=?", (case_id,)
        ).fetchone()["v"])
        report_id = f"RPT-{uuid.uuid4().hex[:12].upper()}"
        db.execute(
            """
            INSERT INTO claim_case_reports(
                report_id, case_id, version, status, report_json, generated_at, generated_by
            ) VALUES (?, ?, ?, 'FINAL_DRAFT', ?, ?, ?)
            """,
            (report_id, case_id, version, _safe_json(report), report["generated_at"], generated_by),
        )
        _activity(
            db, case_id, "REPORT_GENERATED", f"Investigation report v{version} generated",
            f"Report {report_id} contains the claim, validations, evidence, plate observations and full audit trail.",
            actor=generated_by, payload={"report_id": report_id, "version": version},
        )
        _queue_entity(db, "claim_case_reports", report_id, "INSERT", report, generated_by)
    return {"report_id": report_id, "version": version, "report": report, "workspace": _case_payload(case_id)}


@router.get("/api/fraud/cases/{case_id}/report/latest")
def latest_case_report(case_id: str):
    _get_case(case_id)
    with connect() as db:
        row = _row(db.execute(
            "SELECT * FROM claim_case_reports WHERE case_id=? ORDER BY version DESC LIMIT 1", (case_id,)
        ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail="no report generated yet")
    row["report"] = _json(row.pop("report_json"), {})
    return row


@router.get("/api/fraud/cases/{case_id}/database")
def case_database(case_id: str):
    _get_case(case_id)
    return _database_summary(case_id)
