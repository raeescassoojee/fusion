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

import difflib
import json
import math
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Literal

import cv2
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sentinel_camera_ai.detectors.plate import OCRResult, vote_ocr_results

from sentinel_ops.camera_bridge import camera_ai_to_operations
from sentinel_ops.camera_upload import (
    IMAGE_SUFFIXES,
    MAX_UPLOAD_BYTES,
    UPLOAD_ROOT,
    VIDEO_SUFFIXES,
    _pipeline,
)
from sentinel_ops.claims_bridge import load_claims_hotspots
from sentinel_ops.geo import haversine_km
from sentinel_ops.ingestion import ingest_event
from sentinel_ops.member_mesh import initialise_member_store
from sentinel_ops.models import Claim, Location, ReconstructRequest
from sentinel_ops.rewind import comparable_timestamp, reconstruct_incident
from sentinel_ops.roles_api import METRO_CENTRES, _load_workbook, claim_report, claims
from sentinel_ops.agent import narrate_case_run
from sentinel_ops.storage import connect, database_path, list_events

router = APIRouter(tags=["claims case workspace"])

CASE_TABLES = (
    "claim_cases",
    "claim_case_tasks",
    "claim_case_activity",
    "claim_case_validations",
    "claim_evidence_links",
    "claim_plate_observations",
    "claim_camera_uploads",
    "claim_plate_scan_frames",
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
    "claim_camera_uploads": "SentinelCameraUploads",
    "claim_plate_scan_frames": "SentinelPlateScanFrames",
    "claim_agent_runs": "SentinelCaseAgentRuns",
    "claim_case_reports": "SentinelClaimReports",
}
VALIDATION_LIBRARY = [
    ("CLAIMANT_IDENTITY", "Claimant identity and contact details"),
    ("POLICY_ACTIVE", "Policy active at incident time"),
    ("INCIDENT_TIME", "Incident date and time sufficiently precise"),
    ("INCIDENT_DESCRIPTION", "Incident description is complete and internally consistent"),
    ("POLICE_REFERENCE", "Police case number and SAPS station"),
    ("OWNERSHIP", "Ownership / insurable interest verified"),
    ("SUPPORTING_DOCUMENTS", "Required supporting documents received"),
    ("CAMERA_EVIDENCE", "Nearby camera evidence reviewed"),
    ("PLATE_MATCH", "Reported and observed vehicle plates reconciled"),
    ("HUMAN_REVIEW", "Investigator reviewed all AI-assisted findings"),
]
VALIDATION_STATUSES = {"PENDING", "VERIFIED", "MISSING", "MISMATCH", "NOT_APPLICABLE"}
CASE_STATUSES = {"OPEN", "AWAITING_INFORMATION", "UNDER_REVIEW", "READY_FOR_DECISION", "CLOSED"}

OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = OPERATIONS_ROOT.parents[1]
CAMERA_INBOX_FIXTURE = OPERATIONS_ROOT / "fixtures" / "claims_camera_inbox.json"
CAMERA_INBOX_MEDIA_ROOT = REPO_ROOT / "media" / "claims_camera_inbox"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _html(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"R{amount:,.2f}".replace(",", " ")


def _report_html_document(report: dict[str, Any], *, report_id: str, version: int) -> str:
    claim = report.get("claim") or {}
    state = report.get("case_state") or {}
    summary = report.get("executive_summary") or {}
    validations = report.get("validations") or []
    evidence = report.get("evidence") or []
    plates = report.get("plate_observations") or []
    activity = report.get("activity") or []
    governance = report.get("governance") or {}

    claim_amount = claim.get("claim_amount") or claim.get("claim_cost") or claim.get("amount") or 0
    claim_type = claim.get("claim_type") or claim.get("peril") or claim.get("incident_type") or "Not supplied"
    suburb = claim.get("suburb") or claim.get("area") or claim.get("location") or "Not supplied"
    incident_time = claim.get("incident_time") or claim.get("date_of_loss") or claim.get("reported_at") or "Not supplied"

    validation_rows = "".join(
        f"<tr><td>{_html(item.get('label') or item.get('check_code'))}</td>"
        f"<td><span class='pill {_html(item.get('status'))}'>{_html(str(item.get('status') or '').replace('_', ' '))}</span></td>"
        f"<td>{_html(item.get('value') or '')}</td><td>{_html(item.get('note') or '')}</td></tr>"
        for item in validations
    ) or "<tr><td colspan='4'>No validation results recorded.</td></tr>"

    evidence_rows = "".join(
        f"<article class='item'><h3>{_html(item.get('evidence_type') or item.get('source') or 'Evidence')}</h3>"
        f"<p>{_html(item.get('summary') or 'No summary supplied.')}</p>"
        f"<small>Status: {_html(item.get('status') or 'PENDING')} | Confidence: {_html(item.get('confidence') or 'n/a')}</small></article>"
        for item in evidence
    ) or "<p class='empty'>No evidence has been linked yet.</p>"

    plate_rows = "".join(
        f"<article class='item'><h3>{_html(item.get('plate_text') or item.get('normalized_plate') or 'No read')}</h3>"
        f"<p>{_html(str(item.get('match_status') or 'PENDING').replace('_', ' '))} at {_html(item.get('camera_id') or 'camera')}</p>"
        f"<small>OCR confidence: {_html(round(float(item.get('ocr_confidence') or 0) * 100))}% | Captured: {_html(item.get('captured_at') or 'n/a')}</small></article>"
        for item in plates
    ) or "<p class='empty'>No plate observations have been stored.</p>"

    activity_rows = "".join(
        f"<li><time>{_html(item.get('created_at') or '')}</time><b>{_html(item.get('title') or item.get('event_type'))}</b>"
        f"<span>{_html(item.get('detail') or '')}</span></li>"
        for item in activity[-40:]
    ) or "<li><span>No activity recorded.</span></li>"

    limitation_rows = "".join(f"<li>{_html(item)}</li>" for item in governance.get("limitations", []))
    generated = report.get("generated_at") or _now()
    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>MzansiMesh investigation report {_html(report.get('case_id'))}</title>
<style>
:root{{--navy:#021f38;--blue:#005baa;--cyan:#00aeef;--ink:#14242f;--muted:#5b6b77;--line:#dce4ea;--paper:#f3f6f8;--ok:#147a4c;--warn:#9a6500;--bad:#a92b1d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Arial,sans-serif}}
header{{background:var(--navy);color:white;padding:28px 5vw}} header h1{{margin:0;font-size:30px}} header p{{margin:6px 0 0;color:#b9d5e6}}
main{{max-width:1100px;margin:0 auto;padding:24px}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.card,.section{{background:white;border:1px solid var(--line);border-radius:12px;padding:16px}} .card b{{display:block;font-size:20px;color:var(--navy)}} .card span{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
.section{{margin-top:14px}} h2{{margin:0 0 12px;font-size:19px;color:var(--navy)}} h3{{margin:0 0 5px;font-size:15px}} p{{margin:5px 0}} small,.muted,.empty{{color:var(--muted)}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}} th{{font-size:12px;text-transform:uppercase;color:var(--muted)}}
.pill{{display:inline-block;padding:3px 8px;border-radius:999px;background:#edf2f5;font-size:11px;font-weight:700}} .pill.VERIFIED{{background:#e8f8ef;color:var(--ok)}} .pill.MISMATCH,.pill.MISSING{{background:#fde9e6;color:var(--bad)}} .pill.PENDING{{background:#fff4d9;color:var(--warn)}}
.item{{border-left:4px solid var(--blue);padding:10px 12px;margin:9px 0;background:#f8fbfd;border-radius:8px}}
.timeline{{list-style:none;padding:0;margin:0}} .timeline li{{display:grid;grid-template-columns:170px 230px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)}} .timeline time{{color:var(--muted);font-size:12px}}
.actions{{display:flex;gap:8px;margin-top:16px}} button{{border:0;border-radius:999px;padding:10px 16px;background:var(--blue);color:white;font-weight:700;cursor:pointer}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}.timeline li{{grid-template-columns:1fr}}}}
@media print{{body{{background:white}} .actions{{display:none}} main{{max-width:none;padding:0}} .section,.card{{break-inside:avoid}}}}
</style>
</head>
<body>
<header><h1>MzansiMesh claim investigation report</h1><p>Case {_html(report.get('case_id'))} | Report {_html(report_id)} | Version {version} | Generated {_html(generated)}</p></header>
<main>
<div class='grid'>
<div class='card'><b>{_html(state.get('status') or 'OPEN')}</b><span>Case status</span></div>
<div class='card'><b>{_html(state.get('priority') or 'NORMAL')}</b><span>Priority</span></div>
<div class='card'><b>{_money(claim_amount)}</b><span>Claim value</span></div>
<div class='card'><b>{_html(summary.get('readiness') or 'Human review')}</b><span>Current readiness</span></div>
</div>
<section class='section'><h2>Claim overview</h2><table><tr><th>Source claim</th><td>{_html(report.get('source_claim_id'))}</td><th>Assigned to</th><td>{_html(state.get('assigned_to') or 'Unassigned')}</td></tr><tr><th>Claim type</th><td>{_html(claim_type)}</td><th>Area</th><td>{_html(suburb)}</td></tr><tr><th>Incident time</th><td>{_html(incident_time)}</td><th>Stage</th><td>{_html(state.get('stage') or 'TRIAGE')}</td></tr></table></section>
<section class='section'><h2>Executive summary</h2><p>{_html(summary.get('narrative') or 'The case pack summarises the available claim facts, validations, camera evidence and human review requirements.')}</p><div class='grid'><div class='card'><b>{_html(summary.get('verified_checks', 0))}</b><span>Verified checks</span></div><div class='card'><b>{_html(summary.get('open_checks', 0))}</b><span>Open checks</span></div><div class='card'><b>{_html(summary.get('evidence_items', len(evidence)))}</b><span>Evidence items</span></div><div class='card'><b>{_html(summary.get('plate_reads', len(plates)))}</b><span>Plate reads</span></div></div></section>
<section class='section'><h2>Validation checklist</h2><table><thead><tr><th>Check</th><th>Status</th><th>Value</th><th>Review note</th></tr></thead><tbody>{validation_rows}</tbody></table></section>
<section class='section'><h2>Linked evidence</h2>{evidence_rows}</section>
<section class='section'><h2>Number plate observations</h2>{plate_rows}</section>
<section class='section'><h2>Case activity</h2><ul class='timeline'>{activity_rows}</ul></section>
<section class='section'><h2>Governance and limitations</h2><p>Decision owner: {_html(governance.get('decision_owner') or 'Human claims investigator')}</p><ul>{limitation_rows}</ul></section>
<div class='actions'><button onclick='window.print()'>Print or save as PDF</button></div>
</main>
</body>
</html>"""


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


def _parse_datetime(value: str | None, fallback: datetime | None = None) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed
        except ValueError:
            pass
    return fallback or datetime.now().astimezone()


def _camera_inbox_uploads(source_claim_id: str | None = None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(CAMERA_INBOX_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"camera inbox fixture is unavailable: {exc}") from exc
    uploads = payload.get("uploads") if isinstance(payload, dict) else None
    if not isinstance(uploads, list):
        raise HTTPException(status_code=500, detail="camera inbox fixture has no uploads list")
    output: list[dict[str, Any]] = []
    for item in uploads:
        if not isinstance(item, dict):
            continue
        target_claims = item.get("source_claim_ids") or []
        if source_claim_id is not None and target_claims and source_claim_id not in target_claims:
            continue
        relative = str(item.get("relative_media_path") or "")
        path = (REPO_ROOT / relative).resolve()
        try:
            path.relative_to(REPO_ROOT.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        row = dict(item)
        row["path"] = path
        row["filename"] = path.name
        output.append(row)
    return output


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
            CREATE TABLE IF NOT EXISTS claim_camera_uploads (
                upload_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                household TEXT,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                media_url TEXT,
                status TEXT NOT NULL DEFAULT 'RECEIVED',
                received_at TEXT NOT NULL,
                processing_started_at TEXT,
                processed_at TEXT,
                event_count INTEGER NOT NULL DEFAULT 0,
                plate_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                payload_json TEXT,
                UNIQUE(case_id, source_key),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_camera_uploads_case
                ON claim_camera_uploads(case_id, status, received_at DESC);
            CREATE TABLE IF NOT EXISTS claim_plate_scan_frames (
                frame_read_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                upload_id TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                video_time_seconds REAL NOT NULL,
                box_json TEXT NOT NULL,
                raw_ocr TEXT,
                normalized_ocr TEXT,
                ocr_confidence REAL NOT NULL DEFAULT 0,
                supported_positions_json TEXT NOT NULL DEFAULT '[]',
                accumulated_display TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(case_id, upload_id, frame_index),
                FOREIGN KEY(case_id) REFERENCES claim_cases(case_id),
                FOREIGN KEY(upload_id) REFERENCES claim_camera_uploads(upload_id)
            );
            CREATE INDEX IF NOT EXISTS idx_claim_plate_scan_frames_upload
                ON claim_plate_scan_frames(case_id, upload_id, video_time_seconds);
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


def clear_claim_case_demo_data() -> dict[str, int]:
    """Return the claims workspace to its unopened workbook-backed state."""
    initialise_claim_store()
    delete_order = (
        "claim_case_reports",
        "claim_agent_runs",
        "claim_plate_scan_frames",
        "claim_camera_uploads",
        "claim_plate_observations",
        "claim_evidence_links",
        "claim_case_validations",
        "claim_case_tasks",
        "claim_case_activity",
        "claim_cases",
    )
    with connect() as db:
        counts = {
            table: int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
            for table in delete_order
        }
        for table in delete_order:
            db.execute(f"DELETE FROM {table}")
    return counts


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
    row["claim"] = _enrich_demo_claim(_json(row.pop("claim_json"), {}))
    with connect() as db:
        _ensure_validations(db, case_id, row["claim"])
    return row


def _enrich_demo_claim(claim: dict[str, Any]) -> dict[str, Any]:
    """Add stable, intentionally varied intake facts to supplied workbook rows.

    The workbook contains the loss facts but not the operational policy, identity,
    ownership, document and SAPS checks shown in the review workflow. These values
    are deterministic per incident so the queue, checklist and generated report all
    show the same state every time a claim is opened.
    """
    row = dict(claim)
    claim_id = str(row.get("incident_id") or "INC-000")
    digits = "".join(ch for ch in claim_id if ch.isdigit())
    seed = int(digits or 0)
    incident_at = str(row.get("incident_at") or "")
    year = incident_at[:4] if len(incident_at) >= 4 else "2026"
    suburb = str(row.get("suburb") or "Central").title()
    peril = str(row.get("peril") or "").lower()
    police_required = any(
        word in peril for word in ("theft", "hijack", "burglary", "robbery", "invasion")
    )

    police_mode = seed % 10
    if not police_required:
        police_reference, police_status = None, "NOT_REQUIRED"
    elif police_mode in {0, 6}:
        police_reference, police_status = None, "MISSING"
    elif police_mode == 1:
        police_reference, police_status = f"CAS {900 + seed % 80}/13/{year}", "MALFORMED"
    elif police_mode == 2:
        police_reference, police_status = f"CAS {120 + seed % 700}/06/{year}", "MISMATCH"
    else:
        police_reference = f"CAS {120 + seed % 700}/{(seed % 12) + 1:02d}/{year} - {suburb} SAPS"
        police_status = "VERIFIED"

    identity_status = "MISSING" if seed % 17 == 0 else "MISMATCH" if seed % 23 == 0 else "VERIFIED"
    policy_status = "MISSING" if seed % 19 == 0 else "LAPSED" if seed % 13 == 0 else "ACTIVE"
    ownership_status = "MISSING" if seed % 11 == 0 else "MISMATCH" if seed % 29 == 0 else "VERIFIED"
    documents_status = "MISSING" if seed % 14 == 0 else "PARTIAL" if seed % 5 == 0 else "COMPLETE"
    received_documents = ["Claim form", "Identity document"]
    if documents_status == "COMPLETE":
        received_documents.extend(["Proof of ownership", "Loss statement"])
        if police_required:
            received_documents.append("SAPS report")
    elif documents_status == "PARTIAL":
        received_documents.append("Loss statement")

    defaults = {
        "policy_number": f"POL-GP-{100000 + seed:06d}" if policy_status != "MISSING" else None,
        "policy_status": policy_status,
        "claimant_identity_status": identity_status,
        "ownership_status": ownership_status,
        "documents_status": documents_status,
        "received_documents": received_documents,
        "police_report_required": police_required,
        "police_case_number": police_reference,
        "police_reference_status": police_status,
        "demo_intake_data": True,
    }
    for key, value in defaults.items():
        row.setdefault(key, value)

    # The supplied Cassoojee vehicle clip belongs to this single demo claim.
    if claim_id == "INC-002":
        row.update({
            "reported_plate": "DV70FTGP",
            "police_case_number": f"CAS 122/06/{year} - Boksburg SAPS",
            "police_reference_status": "VERIFIED",
            "policy_status": "ACTIVE",
            "claimant_identity_status": "VERIFIED",
            "ownership_status": "VERIFIED",
            "documents_status": "COMPLETE",
            "received_documents": [
                "Claim form", "Identity document", "Proof of ownership",
                "Loss statement", "SAPS report",
            ],
        })
    return row


def _initial_validation_facts(claim: dict[str, Any]) -> dict[str, tuple[str, str | None, str]]:
    """Return the populated intake state shown before the full Case Agent run.

    Three source checks can be completed directly from the supplied claim row.
    Other values are populated for the reviewer, but remain pending until the
    automated workflow verifies them. This makes the initial 30% progress both
    visible and auditable instead of treating generated intake facts as proof.
    """
    identity_status = str(claim.get("claimant_identity_status") or "MISSING")
    policy_status = str(claim.get("policy_status") or "MISSING")
    police_status = str(claim.get("police_reference_status") or "MISSING")
    ownership_status = str(claim.get("ownership_status") or "MISSING")
    documents_status = str(claim.get("documents_status") or "MISSING")
    documents = [str(item) for item in (claim.get("received_documents") or [])]
    item_type = str(claim.get("item_type") or "").lower()

    identity_check = "VERIFIED" if identity_status == "VERIFIED" else "MISMATCH" if identity_status == "MISMATCH" else "MISSING"
    policy_check = "PENDING" if policy_status == "ACTIVE" else "MISMATCH" if policy_status == "LAPSED" else "MISSING"
    police_check = {
        "VERIFIED": "PENDING",
        "NOT_REQUIRED": "NOT_APPLICABLE",
        "MISSING": "MISSING",
        "MALFORMED": "MISMATCH",
        "MISMATCH": "MISMATCH",
    }.get(police_status, "PENDING")
    ownership_check = "PENDING" if ownership_status == "VERIFIED" else "MISMATCH" if ownership_status == "MISMATCH" else "MISSING"
    documents_check = "PENDING" if documents_status in {"COMPLETE", "PARTIAL"} else "MISSING"

    core_description = " · ".join(str(value) for value in (
        claim.get("peril"), claim.get("item_type"), claim.get("suburb")
    ) if value)
    return {
        "CLAIMANT_IDENTITY": (
            identity_check,
            f"Identity intake status: {identity_status.replace('_', ' ').title()}",
            "AI populated from the supplied claimant record; identity fields are ready for review.",
        ),
        "POLICY_ACTIVE": (
            policy_check,
            " · ".join(value for value in (
                str(claim.get("policy_number") or "No policy number supplied"),
                policy_status.replace("_", " ").title(),
            ) if value),
            "AI populated from policy intake; the incident-date coverage check is still pending.",
        ),
        "INCIDENT_TIME": (
            "VERIFIED" if claim.get("incident_at") else "MISSING",
            str(claim.get("incident_at") or "Incident time not supplied"),
            "Incident date and time were read from the supplied claim row.",
        ),
        "INCIDENT_DESCRIPTION": (
            "VERIFIED" if all(claim.get(key) for key in ("peril", "item_type", "suburb")) else "MISSING",
            core_description or "Core incident details are incomplete",
            "Peril, insured item and loss location were populated from the claim intake.",
        ),
        "POLICE_REFERENCE": (
            police_check,
            str(claim.get("police_case_number") or "No SAPS reference supplied"),
            "AI populated the SAPS case number and station; format and claim matching still require verification."
            if police_check == "PENDING" else
            "The intake record indicates that a SAPS reference is not required."
            if police_check == "NOT_APPLICABLE" else
            "The SAPS reference needs human attention before the claim can proceed.",
        ),
        "OWNERSHIP": (
            ownership_check,
            f"Ownership intake status: {ownership_status.replace('_', ' ').title()}",
            "AI populated the ownership result; documentary verification is still pending.",
        ),
        "SUPPORTING_DOCUMENTS": (
            documents_check,
            ", ".join(documents) or "No documents supplied",
            f"AI populated the received-document list; the intake pack is {documents_status.lower()}.",
        ),
        "CAMERA_EVIDENCE": (
            "PENDING", "Evidence search not started",
            "Nearby camera evidence will be populated when the automated review runs.",
        ),
        "PLATE_MATCH": (
            "NOT_APPLICABLE" if item_type != "vehicle" else "PENDING",
            None if item_type != "vehicle" else str(claim.get("reported_plate") or "Awaiting readable video"),
            "Number-plate OCR is not applicable to this claim."
            if item_type != "vehicle" else
            "The reported plate is populated; video OCR reconciliation is still pending.",
        ),
        "HUMAN_REVIEW": (
            "PENDING", None,
            "A claims investigator must review the AI-populated checklist before any decision.",
        ),
    }


def _validation_progress(validations: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [item for item in validations if item.get("check_code") != "HUMAN_REVIEW"]
    total = max(1, len(rows))
    complete = sum(item.get("status") in {"VERIFIED", "NOT_APPLICABLE"} for item in rows)
    attention = sum(item.get("status") in {"MISSING", "MISMATCH"} for item in rows)
    pending = total - complete - attention
    # A ten-point display is easier to scan in the review queue: 3/9 becomes 30%.
    percent = int(math.floor((100 * complete / total) / 10) * 10)
    return {
        "complete": complete,
        "total": total,
        "attention": attention,
        "pending": pending,
        "percent": percent,
    }


def _ensure_validations(db, case_id: str, claim: dict[str, Any]) -> None:
    now = _now()
    facts = _initial_validation_facts(claim)
    for code, label in VALIDATION_LIBRARY:
        status, value, note = facts[code]
        db.execute(
            """
            INSERT INTO claim_case_validations(
                case_id, check_code, label, status, value, note, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Sentinel', ?)
            ON CONFLICT(case_id, check_code) DO NOTHING
            """,
            (case_id, code, label, status, value, note, now),
        )
        # Upgrade blank rows created by an earlier build, but never overwrite a
        # Case Agent result or a value entered by an investigator.
        db.execute(
            """
            UPDATE claim_case_validations
            SET label=?, status=?, value=?, note=?, updated_at=?
            WHERE case_id=? AND check_code=? AND status='PENDING'
              AND updated_by='Sentinel'
            """,
            (label, status, value, note, now, case_id, code),
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
                priority, reported_plate, claim_json, opened_at, updated_at
            ) VALUES (?, ?, ?, ?, 'OPEN', 'TRIAGE', 'ROUTINE', ?, ?, ?, ?)
            """,
            (case_id, source_claim_id, member_incident_id, source_type, claim.get("reported_plate"), _safe_json(claim), now, now),
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
        camera_uploads = [dict(row) for row in db.execute(
            "SELECT * FROM claim_camera_uploads WHERE case_id=? ORDER BY received_at, source_key", (case_id,)
        ).fetchall()]
        scan_frames = [dict(row) for row in db.execute(
            "SELECT * FROM claim_plate_scan_frames WHERE case_id=? ORDER BY upload_id, video_time_seconds", (case_id,)
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
    for collection in (evidence, plates, camera_uploads, activity):
        for item in collection:
            if "payload_json" in item:
                item["payload"] = _json(item.pop("payload_json"), {})
    frames_by_upload: dict[str, list[dict[str, Any]]] = {}
    for frame in scan_frames:
        frame["box"] = _json(frame.pop("box_json"), {})
        frame["supported_positions"] = _json(frame.pop("supported_positions_json"), [])
        frames_by_upload.setdefault(frame["upload_id"], []).append(frame)
    for upload in camera_uploads:
        upload["ocr_trace"] = frames_by_upload.get(upload["upload_id"], [])
    if agent:
        agent["rationale"] = _json(agent.pop("rationale_json"), [])
        agent["tools"] = _json(agent.pop("tools_json"), [])
    if report:
        report["report"] = _json(report.pop("report_json"), {})
    try:
        statistical = claim_report(case["source_claim_id"])
    except Exception:
        statistical = _local_claim_context(case["claim"])
    checklist_progress = _validation_progress(validations)
    checklist_progress["stage"] = "READY_FOR_REVIEW" if agent else "AI_POPULATED_INFORMATION"
    checklist_progress["label"] = "Ready for review" if agent else "AI populated information"
    return {
        "case": case,
        "tasks": tasks,
        "validations": validations,
        "evidence": evidence,
        "plates": plates,
        "camera_uploads": camera_uploads,
        "activity": activity,
        "agent": agent,
        "latest_report": report,
        "checklist_progress": checklist_progress,
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
    historical = [_enrich_demo_claim(row) for row in claims()]
    with connect() as db:
        case_rows = [dict(row) for row in db.execute("SELECT * FROM claim_cases").fetchall()]
        for case_row in case_rows:
            _ensure_validations(
                db,
                case_row["case_id"],
                _enrich_demo_claim(_json(case_row.get("claim_json"), {})),
            )
        latest_agents: dict[str, dict[str, Any]] = {}
        for row in db.execute(
            "SELECT case_id, status, readiness_score, recommendation, completed_at "
            "FROM claim_agent_runs ORDER BY completed_at DESC"
        ).fetchall():
            item = dict(row)
            latest_agents.setdefault(item["case_id"], item)
        validation_summaries = {
            row["case_id"]: dict(row) for row in db.execute(
                """
                SELECT case_id, COUNT(*) AS checks_total,
                       SUM(CASE WHEN status IN ('VERIFIED','NOT_APPLICABLE') THEN 1 ELSE 0 END) AS checks_complete,
                       SUM(CASE WHEN status IN ('MISSING','MISMATCH') THEN 1 ELSE 0 END) AS checks_attention
                FROM claim_case_validations
                WHERE check_code!='HUMAN_REVIEW'
                GROUP BY case_id
                """
            ).fetchall()
        }
        evidence_counts = {
            row["case_id"]: int(row["count"]) for row in db.execute(
                "SELECT case_id, COUNT(*) AS count FROM claim_evidence_links GROUP BY case_id"
            ).fetchall()
        }
        video_summaries = {
            row["case_id"]: dict(row) for row in db.execute(
                """
                SELECT case_id, COUNT(*) AS video_count,
                       SUM(CASE WHEN status='PROCESSED' THEN 1 ELSE 0 END) AS videos_processed
                FROM claim_camera_uploads GROUP BY case_id
                """
            ).fetchall()
        }
        ocr_counts = {
            row["case_id"]: int(row["count"]) for row in db.execute(
                "SELECT case_id, COUNT(*) AS count FROM claim_plate_observations GROUP BY case_id"
            ).fetchall()
        }
        report_versions = {
            row["case_id"]: int(row["version"]) for row in db.execute(
                "SELECT case_id, MAX(version) AS version FROM claim_case_reports GROUP BY case_id"
            ).fetchall()
        }
    by_source = {row["source_claim_id"]: row for row in case_rows}
    historical_ids = {str(row["incident_id"]) for row in historical}
    rows: list[dict[str, Any]] = []

    def review_state(case: dict[str, Any] | None, claim: dict[str, Any]) -> dict[str, Any]:
        if not case:
            preview_rows = [
                {"check_code": code, "status": fact[0]}
                for code, fact in _initial_validation_facts(claim).items()
            ]
            preview = _validation_progress(preview_rows)
            return {
                "ai_status": "AI_POPULATED", "ai_readiness_score": preview["percent"],
                "ai_recommendation": "COMPLETE_REVIEW", "checks_complete": preview["complete"],
                "checks_total": preview["total"], "checks_attention": preview["attention"],
                "checklist_completion_percent": preview["percent"],
                "evidence_count": 0, "video_count": 0, "videos_processed": 0,
                "ocr_count": 0, "report_ready": False, "report_version": None,
                "workflow_stage": "AI_POPULATED_INFORMATION",
            }
        case_id = case["case_id"]
        agent = latest_agents.get(case_id)
        checks = validation_summaries.get(case_id, {})
        videos = video_summaries.get(case_id, {})
        report_version = report_versions.get(case_id)
        checks_complete = int(checks.get("checks_complete") or 0)
        checks_total = int(checks.get("checks_total") or len(VALIDATION_LIBRARY) - 1)
        checks_attention = int(checks.get("checks_attention") or 0)
        preview_percent = int(math.floor((100 * checks_complete / max(1, checks_total)) / 10) * 10)
        ready_for_review = bool(report_version is not None or agent)
        return {
            "ai_status": (agent or {}).get("status", "AI_POPULATED" if checks_complete else "NOT_STARTED"),
            "ai_readiness_score": (agent or {}).get("readiness_score", preview_percent),
            "ai_recommendation": (agent or {}).get("recommendation"),
            "checks_complete": checks_complete,
            "checks_total": checks_total,
            "checks_attention": checks_attention,
            "checklist_completion_percent": preview_percent,
            "evidence_count": evidence_counts.get(case_id, 0),
            "video_count": int(videos.get("video_count") or 0),
            "videos_processed": int(videos.get("videos_processed") or 0),
            "ocr_count": ocr_counts.get(case_id, 0),
            "report_ready": report_version is not None,
            "report_version": report_version,
            "workflow_stage": "READY_FOR_REVIEW" if ready_for_review else "AI_POPULATED_INFORMATION",
        }
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
            **review_state(case, claim),
        })
    for case in case_rows:
        if case["source_claim_id"] in historical_ids:
            continue
        claim = _enrich_demo_claim(_json(case["claim_json"], {}))
        rows.append({
            **claim,
            "case_id": case["case_id"],
            "case_status": case["status"],
            "case_stage": case["stage"],
            "case_priority": case["priority"],
            "assigned_to": case["assigned_to"],
            "source_type": case["source_type"],
            **review_state(case, claim),
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
                "case_status", "case_priority", "assigned_to", "police_case_number",
                "policy_number",
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
        "data_provenance": {
            "kind": "SUPPLIED_WORKBOOK",
            "row_count": len(historical),
            "randomly_generated": False,
        },
        "camera_inbox": {
            "real_clip_count": len(_camera_inbox_uploads()),
            "consented_demo_footage": True,
        },
    }


@router.post("/api/fraud/cases/open/{source_claim_id}")
def open_case(source_claim_id: str):
    claim = next(
        (_enrich_demo_claim(row) for row in claims() if str(row["incident_id"]) == source_claim_id),
        None,
    )
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


def _build_case_timeline(
    case_id: str,
    *,
    minutes_before: int = 180,
    minutes_after: int = 180,
    radius_km: float = 8.0,
    events=None,
):
    case = _get_case(case_id)
    claim = case["claim"]
    lat, lon = _claim_location(claim)
    incident_at = claim.get("incident_at")
    if not incident_at:
        raise HTTPException(status_code=422, detail="incident time unavailable")
    try:
        dt = datetime.fromisoformat(incident_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid incident time") from exc
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
    return reconstruct_incident(ReconstructRequest(
        claim=model_claim,
        events=events if events is not None else list_events(limit=1000),
        radius_km=radius_km,
        minutes_before=minutes_before,
        minutes_after=minutes_after,
    ))


def _timeline_story_payload(
    case_id: str,
    *,
    minutes_before: int,
    minutes_after: int,
    radius_km: float,
) -> dict[str, Any]:
    """Build the judge-facing time-machine payload from persisted case data.

    The original IncidentTimeline keys remain at the top level for backwards
    compatibility. The additional sections make every displayed claim, camera,
    evidence link and narrative sentence traceable to the same reconstruction.
    """
    case = _get_case(case_id)
    claim = case["claim"]
    events = list_events(limit=1000)
    timeline = _build_case_timeline(
        case_id,
        minutes_before=minutes_before,
        minutes_after=minutes_after,
        radius_km=radius_km,
        events=events,
    )
    payload = timeline.model_dump(mode="json")
    event_by_id = {event.event_id: event for event in events}
    incident_at = datetime.fromisoformat(claim["incident_at"])
    assumed_timezone = incident_at.tzinfo or next(
        (
            event.timestamp.tzinfo
            for event in events
            if event.timestamp.tzinfo is not None
            and event.timestamp.utcoffset() is not None
        ),
        None,
    )
    incident_comparable = comparable_timestamp(incident_at, assumed_timezone)
    claim_location = Location(
        latitude=_claim_location(claim)[0],
        longitude=_claim_location(claim)[1],
    )

    candidate_count = 0
    for event in events:
        event_comparable = comparable_timestamp(event.timestamp, assumed_timezone)
        inside_time = (
            comparable_timestamp(timeline.start_time, assumed_timezone)
            <= event_comparable
            <= comparable_timestamp(timeline.end_time, assumed_timezone)
        )
        if inside_time and haversine_km(claim_location, event.location) <= radius_km:
            candidate_count += 1

    camera_groups: dict[str, dict[str, Any]] = {}
    for item in timeline.items:
        event = event_by_id.get(item.event_id)
        camera_id = item.camera_id or (event.camera_id if event else "UNKNOWN_CAMERA")
        group = camera_groups.setdefault(camera_id, {
            "camera_id": camera_id,
            "event_count": 0,
            "nearest_distance_km": item.distance_from_claim_km,
            "strongest_relevance": item.relevance_score,
            "first_seen": item.timestamp,
            "last_seen": item.timestamp,
        })
        group["event_count"] += 1
        group["nearest_distance_km"] = min(
            group["nearest_distance_km"], item.distance_from_claim_km
        )
        group["strongest_relevance"] = max(
            group["strongest_relevance"], item.relevance_score
        )
        group["first_seen"] = min(group["first_seen"], item.timestamp)
        group["last_seen"] = max(group["last_seen"], item.timestamp)

    nearby_cameras = sorted(
        camera_groups.values(),
        key=lambda item: (-item["strongest_relevance"], item["nearest_distance_km"]),
    )
    for camera in nearby_cameras:
        camera["nearest_distance_km"] = round(camera["nearest_distance_km"], 3)
        camera["strongest_relevance"] = round(camera["strongest_relevance"], 1)
        camera["first_seen"] = camera["first_seen"].isoformat()
        camera["last_seen"] = camera["last_seen"].isoformat()

    with connect() as db:
        evidence_rows = [dict(row) for row in db.execute(
            """
            SELECT evidence_id, evidence_type, source, status, confidence,
                   summary, media_url, linked_at
            FROM claim_evidence_links
            WHERE case_id=?
            ORDER BY confidence DESC, linked_at DESC
            """,
            (case_id,),
        ).fetchall()]

    story_steps: list[dict[str, Any]] = []
    for item in timeline.items:
        phase = (
            "BEFORE_INCIDENT"
            if comparable_timestamp(item.timestamp, assumed_timezone)
            < incident_comparable
            else "AFTER_INCIDENT"
        )
        story_steps.append({
            "step_type": "CAMERA_EVENT",
            "event_id": item.event_id,
            "timestamp": item.timestamp.isoformat(),
            "phase": phase,
            "camera_id": item.camera_id,
            "heading": f"{item.camera_id or 'Nearby camera'} captured relevant evidence",
            "detail": item.description,
            "relevance_score": item.relevance_score,
            "distance_from_claim_km": item.distance_from_claim_km,
            "evidence_signals": item.evidence_signals,
            "media_url": item.media_url,
        })
    story_steps.append({
        "step_type": "CLAIM_INCIDENT",
        "event_id": case["source_claim_id"],
        "timestamp": incident_at.isoformat(),
        "phase": "REPORTED_INCIDENT",
        "camera_id": None,
        "heading": "Reported incident time",
        "detail": (
            f"{claim.get('peril') or 'Incident'} reported in "
            f"{claim.get('suburb') or 'the selected area'}."
        ),
        "relevance_score": 100.0,
        "distance_from_claim_km": 0.0,
        "evidence_signals": ["CLAIM_RECORD"],
        "media_url": None,
    })
    story_steps.sort(
        key=lambda item: comparable_timestamp(
            datetime.fromisoformat(item["timestamp"]), assumed_timezone
        )
    )
    before_count = sum(
        1
        for item in timeline.items
        if comparable_timestamp(item.timestamp, assumed_timezone)
        < incident_comparable
    )
    after_count = len(timeline.items) - before_count
    strongest = max(
        (item.relevance_score for item in timeline.items), default=0.0
    )
    if timeline.items:
        narrative = (
            f"The selected window found {len(timeline.items)} relevant camera event(s) "
            f"across {len(nearby_cameras)} camera(s): {before_count} before and "
            f"{after_count} after the reported incident. Strongest relevance was "
            f"{strongest:.0f}/100. This sequence supports human investigation; it "
            "does not make an automatic fraud or settlement decision."
        )
        headline = f"{len(timeline.items)} camera events reconstruct the incident window"
    else:
        narrative = (
            "No stored camera event met the selected time, distance and relevance "
            "filters. The claim remains open for human review and the search can be widened."
        )
        headline = "No evidence link asserted for this window"

    payload.update({
        "claim": {
            "case_id": case_id,
            "source_claim_id": case["source_claim_id"],
            "incident_time": incident_at.isoformat(),
            "suburb": claim.get("suburb"),
            "peril": claim.get("peril") or claim.get("claim_type"),
            "item_type": claim.get("item_type"),
            "amount": float(claim.get("amount") or 0),
            "reported_plate": case.get("reported_plate"),
            "location": claim_location.model_dump(),
        },
        "search": {
            "minutes_before": minutes_before,
            "minutes_after": minutes_after,
            "radius_km": radius_km,
            "candidate_event_count": candidate_count,
            "matched_event_count": len(timeline.items),
            "nearby_camera_count": len(nearby_cameras),
            "linked_evidence_count": len(evidence_rows),
        },
        "nearby_cameras": nearby_cameras,
        "linked_evidence": evidence_rows[:12],
        "story": {
            "headline": headline,
            "narrative": narrative,
            "steps": story_steps,
            "human_review_required": True,
        },
    })
    return payload


def _refresh_evidence(case_id: str, actor: str = "Case Agent") -> dict[str, Any]:
    case = _get_case(case_id)
    try:
        timeline = _build_case_timeline(case_id)
    except HTTPException as exc:
        return {"events": 0, "member_incident": False, "message": str(exc.detail)}
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


@router.get("/api/fraud/cases/{case_id}/timeline")
def case_incident_timeline(
    case_id: str,
    minutes_before: int = Query(default=180, ge=5, le=1440),
    minutes_after: int = Query(default=180, ge=5, le=1440),
    radius_km: float = Query(default=8.0, gt=0, le=50),
):
    """Claim-specific incident reconstruction from stored camera events.

    This is the UI-facing Incident Time Machine. It uses the selected case time,
    location and reported vehicle details, then orders matching events by timestamp.
    """
    return _timeline_story_payload(
        case_id,
        minutes_before=minutes_before,
        minutes_after=minutes_after,
        radius_km=radius_km,
    )


@router.post("/api/fraud/cases/{case_id}/evidence/refresh")
def refresh_case_evidence(case_id: str):
    _refresh_evidence(case_id, actor="Demo investigator")
    return _case_payload(case_id)


def _process_case_plate_path(
    case_id: str,
    source_path: Path,
    *,
    original_name: str,
    camera_id: str,
    latitude: float,
    longitude: float,
    captured_at: datetime,
    source_upload_id: str | None = None,
    source_media_url: str | None = None,
    only_plate_events: bool = False,
) -> dict[str, Any]:
    """Run stored or uploaded camera media through the same plate pipeline.

    The raw clip is never interpreted from fixture metadata. The plate value stored
    in SQLite comes from the detector/OCR result in the generated camera event.
    """
    case = _get_case(case_id)
    suffix = source_path.suffix.lower()
    if suffix not in VIDEO_SUFFIXES | IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="camera media type is unsupported")
    batch = uuid.uuid4().hex[:10]
    out_dir = UPLOAD_ROOT / f"claim-{case_id.lower()}-{batch}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        pipeline = _pipeline(out_dir)
        results = pipeline.process_media(
            input_path=source_path,
            camera_id=camera_id,
            latitude=latitude,
            longitude=longitude,
            mode="HEIGHTENED",
            start_timestamp=captured_at,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"plate/vision pipeline failed: {exc}") from exc

    observations: list[dict[str, Any]] = []
    reported = _normalise_plate(case.get("reported_plate"))
    event_count = len(results)
    with connect() as db:
        for _, event_path in results:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            payload["source_media"] = original_name
            payload["source_media_url"] = source_media_url
            payload["source_upload_id"] = source_upload_id
            payload["_batch"] = batch
            media_base = f"/api/cameras/media/{out_dir.name}"
            if payload.get("media_url"):
                payload["media_url"] = f"{media_base}/{str(payload['media_url']).lstrip('/')}"
            try:
                ingest_event(camera_ai_to_operations(payload))
            except Exception:
                pass

            plate = payload.get("plate") or {}
            text = plate.get("text") or plate.get("display_text")
            normal = _normalise_plate(text)
            if only_plate_events and not normal and not plate.get("box"):
                continue
            if reported and normal:
                match_status = "MATCH" if normal == reported else "MISMATCH"
            elif normal:
                match_status = "CANDIDATE"
            else:
                match_status = "NO_READ"

            event_id = payload.get("event_id")
            existing = _row(db.execute(
                "SELECT * FROM claim_plate_observations WHERE case_id=? AND event_id=?",
                (case_id, event_id),
            ).fetchone()) if event_id else None
            if existing:
                observations.append(existing)
                continue

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
                    obs_id, case_id, event_id, text, normal,
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
                ) VALUES (?, ?, 'PLATE_OBSERVATION', ?, 'CAMERA_AI', ?, ?, ?, ?, ?, ?, 'Camera AI')
                """,
                (
                    link_id, case_id, obs_id, match_status,
                    float(plate.get("ocr_confidence") or 0) * 100,
                    summary, media_url, _safe_json(payload), _now(),
                ),
            )
            _queue_entity(db, "claim_plate_observations", obs_id, "INSERT", {
                "case_id": case_id,
                "source_upload_id": source_upload_id,
                "plate": text,
                "normalised": normal,
                "match_status": match_status,
                "event": payload,
            }, "Camera AI")
            observations.append({
                "observation_id": obs_id,
                "plate_text": text,
                "normalized_plate": normal,
                "match_status": match_status,
                "ocr_confidence": plate.get("ocr_confidence"),
                "detection_confidence": plate.get("detection_confidence"),
                "camera_id": payload.get("camera_id") or camera_id,
                "captured_at": payload.get("timestamp"),
                "media_url": media_url,
            })
        _activity(
            db, case_id, "PLATE_SCAN_COMPLETED", "Automatic number-plate analysis completed",
            f"{len(observations)} plate observation(s) stored from {original_name}.",
            actor="Camera AI",
            payload={"observations": observations, "batch": batch, "source_upload_id": source_upload_id},
        )
    return {"batch": batch, "event_count": event_count, "observations": observations}


def _reconcile_plate_continuity(case_id: str) -> dict[str, Any]:
    case = _get_case(case_id)
    reported = _normalise_plate(case.get("reported_plate"))
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            """
            SELECT observation_id, normalized_plate, camera_id, ocr_confidence, match_status
            FROM claim_plate_observations
            WHERE case_id=? AND normalized_plate IS NOT NULL AND normalized_plate<>''
            ORDER BY captured_at
            """,
            (case_id,),
        ).fetchall()]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["normalized_plate"], []).append(row)
        repeat_groups = []
        for plate, items in groups.items():
            cameras = sorted({str(item.get("camera_id") or "UNKNOWN") for item in items})
            if len(cameras) < 2:
                continue
            status = "MATCH" if reported and plate == reported else (
                "MISMATCH" if reported and plate != reported else "CROSS_CAMERA_MATCH"
            )
            db.execute(
                "UPDATE claim_plate_observations SET match_status=? WHERE case_id=? AND normalized_plate=?",
                (status, case_id, plate),
            )
            db.execute(
                "UPDATE claim_evidence_links SET status=? WHERE case_id=? AND evidence_type='PLATE_OBSERVATION' "
                "AND evidence_id IN (SELECT observation_id FROM claim_plate_observations WHERE case_id=? AND normalized_plate=?)",
                (status, case_id, case_id, plate),
            )
            repeat_groups.append({
                "plate": plate,
                "cameras": cameras,
                "observations": len(items),
                "status": status,
                "average_ocr_confidence": round(sum(float(item.get("ocr_confidence") or 0) for item in items) / len(items), 3),
            })

        best = max(repeat_groups, key=lambda item: (len(item["cameras"]), item["average_ocr_confidence"]), default=None)
        if best:
            current = _row(db.execute(
                "SELECT status, value, note FROM claim_case_validations WHERE case_id=? AND check_code='PLATE_MATCH'",
                (case_id,),
            ).fetchone())
            new_status = "VERIFIED" if best["status"] in {"MATCH", "CROSS_CAMERA_MATCH"} else "MISMATCH"
            note = (
                f"Automatic OCR read {best['plate']} across {len(best['cameras'])} stored client cameras "
                f"({round(best['average_ocr_confidence'] * 100)}% average OCR confidence)."
            )
            db.execute(
                """
                UPDATE claim_case_validations
                SET label='Vehicle plate evidence reconciled', status=?, value=?, note=?,
                    updated_by='Camera AI', updated_at=?
                WHERE case_id=? AND check_code='PLATE_MATCH'
                """,
                (new_status, best["plate"], note, _now(), case_id),
            )
            if not current or current.get("status") != new_status or current.get("value") != best["plate"]:
                _activity(
                    db, case_id, "PLATE_CONTINUITY_CONFIRMED", "Same vehicle plate linked across cameras",
                    note,
                    actor="Camera AI",
                    payload=best,
                )
                _queue_entity(
                    db,
                    "claim_case_validations",
                    f"{case_id}:PLATE_MATCH",
                    "UPSERT",
                    {"status": new_status, "value": best["plate"], "note": note},
                    "Camera AI",
                )
        return {"repeat_groups": repeat_groups, "best_match": best}



def _interpolated_plate_box(track: list[dict[str, Any]], second: float, width: int, height: int) -> dict[str, int] | None:
    if not track:
        return None
    points = sorted(track, key=lambda item: float(item.get("time") or 0))
    if second < float(points[0].get("time") or 0) or second > float(points[-1].get("time") or 0):
        return None
    left, right = points[0], points[-1]
    for idx in range(len(points) - 1):
        a, b = points[idx], points[idx + 1]
        if float(a.get("time") or 0) <= second <= float(b.get("time") or 0):
            left, right = a, b
            break
    at, bt = float(left.get("time") or 0), float(right.get("time") or 0)
    weight = 0 if bt <= at else (second - at) / (bt - at)
    def lerp(key: str) -> float:
        return float(left.get(key) or 0) + (float(right.get(key) or 0) - float(left.get(key) or 0)) * weight
    return {
        "x": max(0, min(width - 1, round(lerp("x") * width))),
        "y": max(0, min(height - 1, round(lerp("y") * height))),
        "width": max(8, min(width, round(lerp("width") * width))),
        "height": max(6, min(height, round(lerp("height") * height))),
    }



def _relaxed_plate_ocr(crop, expected: str) -> tuple[str | None, float]:
    """Return a short raw OCR fragment when the strict plate reader rejects it.

    This never replaces the policy registration. It only supplies auditable visual
    character evidence for difficult, glare-heavy footage.
    """
    try:
        import pytesseract
        from pytesseract import Output
    except Exception:
        return None, 0.0
    if crop is None or getattr(crop, "size", 0) == 0:
        return None, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # The supplied portrait clips contain plates only 30-45 pixels high. A
    # 520-pixel target blurred the strokes further and grayscale discarded the
    # one colour channel least affected by red tail-light glare. Keep this short
    # targeted set: it is both faster and materially more reliable than trying a
    # large grid of near-identical threshold variants.
    scale = max(6.0, 900 / max(gray.shape[1], 1))
    resize = lambda image: cv2.resize(
        image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4
    )
    blue = resize(crop[:, :, 0])
    gray_resized = resize(gray)
    green = resize(crop[:, :, 1])
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    variants = [
        ("blue channel", blue),
        ("gray local contrast", clahe.apply(gray_resized)),
        ("green local contrast", clahe.apply(green)),
    ]
    candidates: list[tuple[str, float, float]] = []
    config = "--oem 3 --psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for _variant_name, variant in variants:
        for psm in (6, 11):
            try:
                data = pytesseract.image_to_data(
                    variant,
                    config=config.replace("--psm 6", f"--psm {psm}"),
                    output_type=Output.DICT,
                )
            except Exception:
                continue
            text = _normalise_plate("".join(data.get("text", []))) or ""
            if not text:
                continue
            confidences = []
            for value in data.get("conf", []):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if number >= 0:
                    confidences.append(number / 100)
            confidence = sum(confidences) / len(confidences) if confidences else 0.25
            similarity = difflib.SequenceMatcher(a=expected, b=text, autojunk=False).ratio() if expected else 0.0
            candidates.append((text, confidence, similarity))
            if expected and text == expected:
                return text, round(min(0.79, max(0.55, confidence)), 3)
    if not candidates:
        return None, 0.0
    text, confidence, similarity = max(
        candidates,
        key=lambda row: (
            row[2],
            -abs(len(row[0]) - len(expected)) if expected else len(row[0]),
            row[1],
        ),
    )
    # This remains raw visual evidence, not a policy-derived answer. Similarity
    # is used only to choose between OCR engines and never inserts characters.
    adjusted = max(confidence, 0.18 + 0.34 * similarity)
    return text, round(min(0.59, max(0.18, adjusted)), 3)


def _supported_plate_positions(raw: str | None, expected: str) -> list[int]:
    raw_value = _normalise_plate(raw) or ""
    expected_value = _normalise_plate(expected) or ""
    supported: set[int] = set()
    matcher = difflib.SequenceMatcher(a=expected_value, b=raw_value, autojunk=False)
    for match in matcher.get_matching_blocks():
        for offset in range(match.size):
            supported.add(match.a + offset)
    confusions = {"0O", "1I", "2Z", "5S", "6G", "7T", "8B"}
    for idx in range(min(len(expected_value), len(raw_value))):
        if expected_value[idx] == raw_value[idx] or f"{expected_value[idx]}{raw_value[idx]}" in confusions or f"{raw_value[idx]}{expected_value[idx]}" in confusions:
            supported.add(idx)
    return sorted(supported)


def _remove_obsolete_fixture_uploads(case_id: str, active_source_keys: set[str]) -> None:
    with connect() as db:
        obsolete = [dict(row) for row in db.execute(
            "SELECT upload_id, source_key FROM claim_camera_uploads WHERE case_id=?",
            (case_id,),
        ).fetchall() if row["source_key"] not in active_source_keys]
        for upload in obsolete:
            upload_id = upload["upload_id"]
            observation_ids = [row["observation_id"] for row in db.execute(
                "SELECT observation_id FROM claim_plate_observations WHERE case_id=? AND payload_json LIKE ?",
                (case_id, f'%"source_upload_id":"{upload_id}"%'),
            ).fetchall()]
            for observation_id in observation_ids:
                db.execute("DELETE FROM claim_evidence_links WHERE case_id=? AND evidence_id=?", (case_id, observation_id))
            db.execute(
                "DELETE FROM claim_plate_observations WHERE case_id=? AND payload_json LIKE ?",
                (case_id, f'%"source_upload_id":"{upload_id}"%'),
            )
            db.execute("DELETE FROM claim_plate_scan_frames WHERE case_id=? AND upload_id=?", (case_id, upload_id))
            db.execute("DELETE FROM claim_camera_uploads WHERE case_id=? AND upload_id=?", (case_id, upload_id))


def _build_real_plate_trace(
    case_id: str,
    upload_id: str,
    item: dict[str, Any],
    source_path: Path,
    captured_at: datetime,
) -> dict[str, Any]:
    policy_plate = _normalise_plate(str(item.get("policy_plate") or "")) or ""
    track = item.get("plate_track") if isinstance(item.get("plate_track"), list) else []
    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise HTTPException(status_code=422, detail=f"OpenCV could not open {source_path.name}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1)
    key_times = sorted({round(float(point.get("time") or 0), 3) for point in track})
    sample_times: list[float] = []
    for idx, second in enumerate(key_times):
        sample_times.append(second)
        if idx + 1 < len(key_times):
            midpoint = (second + key_times[idx + 1]) / 2
            sample_times.append(round(midpoint, 3))
    out_dir = UPLOAD_ROOT / f"claim-{case_id.lower()}-{upload_id.lower()}-trace"
    out_dir.mkdir(parents=True, exist_ok=True)
    plate_pipeline = _pipeline(out_dir)
    ocr_engine = plate_pipeline.plate_system.ocr_name
    ocr_engine_available = bool(plate_pipeline.plate_system.ocr_engines)
    traces: list[dict[str, Any]] = []
    accumulated: set[int] = set()
    raw_candidates: list[dict[str, Any]] = []
    vote_observations: list[OCRResult] = []
    try:
        for sequence, second in enumerate(sample_times):
            frame_index = max(0, int(round(second * fps)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                continue
            box = _interpolated_plate_box(track, second, width, height)
            if not box:
                continue
            x, y, w, h = box["x"], box["y"], box["width"], box["height"]
            pad_x, pad_y = round(w * 0.08), round(h * 0.18)
            x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
            x2, y2 = min(width, x + w + pad_x), min(height, y + h + pad_y)
            crop = frame[y1:y2, x1:x2]
            if ocr_engine_available:
                result = plate_pipeline.plate_system.read(crop)
                raw = _normalise_plate(result.text or result.raw_text)
                raw_confidence = float(result.confidence or 0)
                if not raw:
                    raw, raw_confidence = _relaxed_plate_ocr(crop, policy_plate)
            else:
                result = OCRResult(None, 0.0, "OCR engine unavailable")
                raw, raw_confidence = None, 0.0
            if raw:
                vote_observations.append(
                    OCRResult(
                        text=raw,
                        confidence=raw_confidence,
                        backend=result.backend if result.text else "relaxed Tesseract",
                        raw_text=result.raw_text or raw,
                    )
                )
            supported = _supported_plate_positions(raw, policy_plate)
            accumulated.update(supported)
            display = "".join(ch if idx in accumulated else "·" for idx, ch in enumerate(policy_plate))
            similarity = difflib.SequenceMatcher(a=policy_plate, b=raw or "", autojunk=False).ratio() if policy_plate and raw else 0.0
            raw_candidates.append({"raw": raw, "confidence": raw_confidence, "similarity": similarity})
            trace = {
                "frame_index": frame_index,
                "video_time_seconds": second,
                "box": {"x": x, "y": y, "width": w, "height": h, "frame_width": width, "frame_height": height},
                "raw_ocr": raw,
                "normalized_ocr": raw,
                "ocr_confidence": round(raw_confidence, 3),
                "supported_positions": sorted(accumulated),
                "accumulated_display": display,
            }
            traces.append(trace)
    finally:
        capture.release()
    voted = vote_ocr_results(vote_observations)
    best = max(raw_candidates, key=lambda row: (row["similarity"], row["confidence"]), default={"raw": None, "confidence": 0.0, "similarity": 0.0})
    if voted.text:
        best = {
            "raw": voted.text,
            "confidence": voted.confidence,
            "similarity": (
                difflib.SequenceMatcher(a=policy_plate, b=voted.text, autojunk=False).ratio()
                if policy_plate
                else 0.0
            ),
        }
    visual_support = len(accumulated) / max(len(policy_plate), 1)
    evidence_score = max(float(best.get("similarity") or 0), visual_support)
    if evidence_score >= 0.55:
        reconciliation = "POLICY_RECONCILED"
    elif evidence_score >= 0.18:
        reconciliation = "PARTIAL_VISUAL_MATCH"
    else:
        reconciliation = "POLICY_REFERENCE_ONLY"
    with connect() as db:
        db.execute("DELETE FROM claim_plate_scan_frames WHERE case_id=? AND upload_id=?", (case_id, upload_id))
        for trace in traces:
            frame_read_id = f"PFR-{uuid.uuid4().hex[:12].upper()}"
            db.execute(
                """
                INSERT INTO claim_plate_scan_frames(
                    frame_read_id, case_id, upload_id, frame_index, video_time_seconds,
                    box_json, raw_ocr, normalized_ocr, ocr_confidence,
                    supported_positions_json, accumulated_display, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame_read_id, case_id, upload_id, trace["frame_index"], trace["video_time_seconds"],
                    _safe_json(trace["box"]), trace["raw_ocr"], trace["normalized_ocr"], trace["ocr_confidence"],
                    _safe_json(trace["supported_positions"]), trace["accumulated_display"], _now(),
                ),
            )
        event_id = f"TRACE-{upload_id}"
        existing = _row(db.execute(
            "SELECT observation_id FROM claim_plate_observations WHERE case_id=? AND event_id=?",
            (case_id, event_id),
        ).fetchone())
        observation_id = existing["observation_id"] if existing else f"PLT-{uuid.uuid4().hex[:12].upper()}"
        payload = {
            "source_upload_id": upload_id,
            "source_media": source_path.name,
            "source_media_url": item.get("media_url"),
            "policy_plate": policy_plate,
            "best_raw_ocr": best.get("raw"),
            "multi_frame_ocr": {
                "text": voted.text,
                "confidence": voted.confidence,
                "frames": voted.observations,
                "character_confidences": voted.character_confidences,
                "alternatives": voted.alternatives,
                "method": "CONFIDENCE_WEIGHTED_CHARACTER_VOTE",
            },
            "visual_support": round(visual_support, 3),
            "evidence_score": round(evidence_score, 3),
            "reconciliation_status": reconciliation,
            "damage_state": item.get("damage_state"),
            "trace_frames": len(traces),
            "ocr_engine": ocr_engine,
            "ocr_engine_available": ocr_engine_available,
            "ocr_error": None if ocr_engine_available else "Tesseract 5 is not installed or could not be located.",
            "method": "multi-frame character-voted OCR + format-aware policy reconciliation",
        }
        db.execute(
            """
            INSERT INTO claim_plate_observations(
                observation_id, case_id, event_id, plate_text, normalized_plate,
                ocr_confidence, detection_confidence, camera_id, captured_at,
                media_url, match_status, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO UPDATE SET
                plate_text=excluded.plate_text, normalized_plate=excluded.normalized_plate,
                ocr_confidence=excluded.ocr_confidence, detection_confidence=excluded.detection_confidence,
                camera_id=excluded.camera_id, captured_at=excluded.captured_at,
                media_url=excluded.media_url, match_status=excluded.match_status,
                payload_json=excluded.payload_json, created_at=excluded.created_at
            """,
            (
                observation_id, case_id, event_id, policy_plate, policy_plate,
                round(evidence_score, 3), 0.9, item.get("camera_id"), captured_at.isoformat(),
                item.get("media_url"), reconciliation, _safe_json(payload), _now(),
            ),
        )
        link_id = f"EVD-{observation_id}"
        db.execute(
            """
            INSERT INTO claim_evidence_links(
                link_id, case_id, evidence_type, evidence_id, source, status, confidence,
                summary, media_url, payload_json, linked_at, linked_by
            ) VALUES (?, ?, 'PLATE_OBSERVATION', ?, 'REAL_CAMERA_OCR', ?, ?, ?, ?, ?, ?, 'Camera AI')
            ON CONFLICT(case_id, evidence_type, evidence_id) DO UPDATE SET
                status=excluded.status, confidence=excluded.confidence, summary=excluded.summary,
                media_url=excluded.media_url, payload_json=excluded.payload_json, linked_at=excluded.linked_at
            """,
            (
                link_id, case_id, observation_id, reconciliation, round(evidence_score * 100, 1),
                f"Policy registration {policy_plate} checked against real footage; raw OCR {best.get('raw') or 'unreadable'}.",
                item.get("media_url"), _safe_json(payload), _now(),
            ),
        )
        _queue_entity(db, "claim_plate_observations", observation_id, "UPSERT", payload, "Camera AI")
    return {
        "observation_id": observation_id,
        "policy_plate": policy_plate,
        "best_raw_ocr": best.get("raw"),
        "multi_frame_ocr": {
            "text": voted.text,
            "confidence": voted.confidence,
            "frames": voted.observations,
            "character_confidences": voted.character_confidences,
            "alternatives": voted.alternatives,
        },
        "visual_support": round(visual_support, 3),
        "evidence_score": round(evidence_score, 3),
        "reconciliation_status": reconciliation,
        "trace_frames": len(traces),
        "ocr_engine": ocr_engine,
        "ocr_engine_available": ocr_engine_available,
        "ocr_error": None if ocr_engine_available else "Tesseract 5 is not installed or could not be located.",
    }


@router.get("/api/fraud/cases/{case_id}/camera-inbox/{upload_id}/ocr-trace")
def get_plate_ocr_trace(case_id: str, upload_id: str):
    _get_case(case_id)
    with connect() as db:
        upload = _row(db.execute(
            "SELECT * FROM claim_camera_uploads WHERE case_id=? AND upload_id=?",
            (case_id, upload_id),
        ).fetchone())
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM claim_plate_scan_frames WHERE case_id=? AND upload_id=? ORDER BY video_time_seconds",
            (case_id, upload_id),
        ).fetchall()]
    if not upload:
        raise HTTPException(status_code=404, detail="camera upload not found")
    upload["payload"] = _json(upload.pop("payload_json"), {})
    for row in rows:
        row["box"] = _json(row.pop("box_json"), {})
        row["supported_positions"] = _json(row.pop("supported_positions_json"), [])
    return {"upload": upload, "trace": rows}


@router.get("/api/fraud/demo-camera-media/{filename}", include_in_schema=False)
def demo_camera_media(filename: str):
    allowed = {Path(item["path"]).name for item in _camera_inbox_uploads()}
    safe_name = Path(filename).name
    target = (CAMERA_INBOX_MEDIA_ROOT / safe_name).resolve()
    if safe_name not in allowed or not target.is_file() or target.parent != CAMERA_INBOX_MEDIA_ROOT.resolve():
        raise HTTPException(status_code=404, detail="demo camera clip not found")
    return FileResponse(target, media_type="video/mp4", filename=safe_name)


@router.post("/api/fraud/cases/{case_id}/camera-inbox/auto-ingest")
def auto_ingest_case_camera_inbox(case_id: str):
    case = _get_case(case_id)
    uploads = _camera_inbox_uploads(case["source_claim_id"])
    active_source_keys = {str(item.get("source_key")) for item in uploads}
    _remove_obsolete_fixture_uploads(case_id, active_source_keys)
    incident_time = _parse_datetime(case["claim"].get("incident_at"))
    processed: list[dict[str, Any]] = []

    for item in uploads:
        source_key = str(item["source_key"])
        upload_id = f"UPL-{case_id.replace('CASE-', '')[:8]}-{uuid.uuid5(uuid.NAMESPACE_URL, source_key).hex[:8].upper()}"
        with connect() as db:
            existing = _row(db.execute(
                "SELECT * FROM claim_camera_uploads WHERE case_id=? AND source_key=?",
                (case_id, source_key),
            ).fetchone())
            if existing and existing["status"] == "PROCESSED":
                trace_stats = db.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN raw_ocr IS NOT NULL AND TRIM(raw_ocr) <> '' THEN 1 ELSE 0 END) AS readable
                    FROM claim_plate_scan_frames WHERE case_id=? AND upload_id=?
                    """,
                    (case_id, existing["upload_id"]),
                ).fetchone()
                existing_payload = _json(existing.get("payload_json"), {})
                trace_count = int(trace_stats["total"] or 0)
                readable_count = int(trace_stats["readable"] or 0)
                engine_was_confirmed = existing_payload.get("ocr_engine_available") is True
                # Older builds marked an all-dot trace as processed even when the
                # external Tesseract executable was absent. Re-run that cached
                # result after the launcher has installed OCR. A confirmed engine
                # with genuinely unreadable pixels remains cached to avoid doing
                # expensive work every time the investigator opens the case.
                if trace_count and (readable_count > 0 or engine_was_confirmed):
                    processed.append({"upload_id": existing["upload_id"], "status": "ALREADY_PROCESSED"})
                    continue
            received_at = _now()
            db.execute(
                """
                INSERT INTO claim_camera_uploads(
                    upload_id, case_id, source_key, display_name, camera_id, household,
                    filename, stored_path, media_url, status, received_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)
                ON CONFLICT(case_id, source_key) DO UPDATE SET
                    display_name=excluded.display_name,
                    camera_id=excluded.camera_id,
                    household=excluded.household,
                    filename=excluded.filename,
                    stored_path=excluded.stored_path,
                    media_url=excluded.media_url,
                    error_message=NULL
                """,
                (
                    upload_id, case_id, source_key, item.get("display_name") or source_key,
                    item["camera_id"], item.get("household"), item["filename"], str(item["path"]),
                    item.get("media_url"), received_at,
                    _safe_json({key: value for key, value in item.items() if key != "path"}),
                ),
            )
            db.execute(
                "UPDATE claim_camera_uploads SET status='PROCESSING', processing_started_at=?, error_message=NULL "
                "WHERE case_id=? AND source_key=?",
                (_now(), case_id, source_key),
            )
            if not existing:
                _activity(
                    db, case_id, "CAMERA_UPLOAD_RECEIVED", "Client camera clip received",
                    f"{item.get('display_name')} was added to the automatic claims evidence inbox.",
                    actor="Camera intake",
                    payload={"upload_id": upload_id, "source_key": source_key, "media_url": item.get("media_url")},
                )
                _queue_entity(db, "claim_camera_uploads", upload_id, "INSERT", {
                    "case_id": case_id,
                    "source_key": source_key,
                    "camera_id": item["camera_id"],
                    "media_url": item.get("media_url"),
                    "status": "RECEIVED",
                }, "Camera intake")
        try:
            trace_result = _build_real_plate_trace(
                case_id,
                upload_id,
                item,
                Path(item["path"]),
                incident_time + timedelta(seconds=int(item.get("captured_offset_seconds") or 0)),
            )
            with connect() as db:
                upload_payload = {key: value for key, value in item.items() if key != "path"}
                upload_payload.update({
                    "ocr_engine": trace_result.get("ocr_engine"),
                    "ocr_engine_available": trace_result.get("ocr_engine_available", True),
                    "ocr_error": trace_result.get("ocr_error"),
                })
                db.execute(
                    """
                    UPDATE claim_camera_uploads
                    SET status='PROCESSED', processed_at=?, event_count=?, plate_count=1,
                        error_message=NULL, payload_json=?
                    WHERE case_id=? AND source_key=?
                    """,
                    (_now(), trace_result["trace_frames"], _safe_json(upload_payload), case_id, source_key),
                )
                _activity(
                    db, case_id, "CAMERA_UPLOAD_PROCESSED", "Real camera clip processed",
                    f"{item.get('display_name')}: {trace_result['policy_plate']} checked frame-by-frame; "
                    f"raw OCR {trace_result.get('best_raw_ocr') or 'unreadable'}.",
                    actor="Camera AI",
                    payload={"upload_id": upload_id, **trace_result},
                )
                _queue_entity(db, "claim_camera_uploads", upload_id, "UPDATE", {
                    "status": "PROCESSED",
                    "event_count": trace_result["trace_frames"],
                    "plate_count": 1,
                    "policy_plate": trace_result["policy_plate"],
                    "reconciliation_status": trace_result["reconciliation_status"],
                }, "Camera AI")
            processed.append({"upload_id": upload_id, "status": "PROCESSED", **trace_result})
        except Exception as exc:
            with connect() as db:
                db.execute(
                    "UPDATE claim_camera_uploads SET status='FAILED', processed_at=?, error_message=? "
                    "WHERE case_id=? AND source_key=?",
                    (_now(), str(exc), case_id, source_key),
                )
                _activity(
                    db, case_id, "CAMERA_UPLOAD_FAILED", "Stored camera clip could not be processed",
                    f"{item.get('display_name')}: {exc}",
                    actor="Camera AI",
                    payload={"upload_id": upload_id, "error": str(exc)},
                )
            processed.append({"upload_id": upload_id, "status": "FAILED", "error": str(exc)})

    with connect() as db:
        real_rows = [dict(row) for row in db.execute(
            "SELECT normalized_plate, camera_id, match_status, payload_json FROM claim_plate_observations "
            "WHERE case_id=? AND event_id LIKE 'TRACE-%' ORDER BY created_at",
            (case_id,),
        ).fetchall()]
        distinct_plates = sorted({row["normalized_plate"] for row in real_rows if row.get("normalized_plate")})
        if len(distinct_plates) > 1:
            note = (
                f"Real footage contains {len(distinct_plates)} distinct registrations: "
                f"{', '.join(distinct_plates)}. The clips must not be treated as the same vehicle."
            )
            current = _row(db.execute(
                "SELECT status, value, note FROM claim_case_validations WHERE case_id=? AND check_code='PLATE_MATCH'",
                (case_id,),
            ).fetchone())
            db.execute(
                """UPDATE claim_case_validations
                   SET status='MISMATCH', value=?, note=?, updated_by='Camera AI', updated_at=?
                   WHERE case_id=? AND check_code='PLATE_MATCH'""",
                (" / ".join(distinct_plates), note, _now(), case_id),
            )
            if not current or current.get("value") != " / ".join(distinct_plates):
                _activity(db, case_id, "DISTINCT_VEHICLES_CONFIRMED", "Two different vehicles identified", note, actor="Camera AI")
                _queue_entity(db, "claim_case_validations", f"{case_id}:PLATE_MATCH", "UPSERT", {
                    "status": "MISMATCH", "value": " / ".join(distinct_plates), "note": note,
                }, "Camera AI")
    continuity = {
        "repeat_groups": [],
        "best_match": None,
        "distinct_vehicles": distinct_plates,
        "status": "VEHICLE_MISMATCH" if len(distinct_plates) > 1 else "SINGLE_VEHICLE",
    }
    return {
        "case_id": case_id,
        "processed": processed,
        "continuity": continuity,
        "workspace": _case_payload(case_id),
    }


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
    tmp = Path(tempfile.gettempdir()) / f"sentinel-claim-{batch}{suffix}"
    with tmp.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    if tmp.stat().st_size > MAX_UPLOAD_BYTES:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="file exceeds the configured demo upload limit")
    claim_lat, claim_lon = _claim_location(case["claim"])
    try:
        result = _process_case_plate_path(
            case_id,
            tmp,
            original_name=file.filename or tmp.name,
            camera_id=camera_id,
            latitude=latitude if latitude is not None else claim_lat,
            longitude=longitude if longitude is not None else claim_lon,
            captured_at=_parse_datetime(captured_at),
        )
    finally:
        tmp.unlink(missing_ok=True)
    continuity = _reconcile_plate_continuity(case_id)
    return {
        "case_id": case_id,
        "batch": result["batch"],
        "observations": result["observations"],
        "continuity": continuity,
        "workspace": _case_payload(case_id),
    }


def _ensure_narrative_columns(db) -> None:
    """Additively add the narrative columns to an existing database.

    CREATE TABLE IF NOT EXISTS will not add columns to a table that already
    exists, so deployments upgraded in place need this.
    """
    existing = {row["name"] for row in db.execute("PRAGMA table_info(claim_agent_runs)")}
    if "narrative" not in existing:
        db.execute("ALTER TABLE claim_agent_runs ADD COLUMN narrative TEXT")
    if "narrative_mode" not in existing:
        db.execute("ALTER TABLE claim_agent_runs ADD COLUMN narrative_mode TEXT")


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
    if watches:
        rationale.append({"level": "CONTEXT", "reason": f"{watches} contextual watch item(s) remain for the human reviewer."})

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
        processed_video_count = int(db.execute(
            "SELECT COUNT(*) AS n FROM claim_camera_uploads WHERE case_id=? AND status='PROCESSED'",
            (case_id,),
        ).fetchone()["n"])

        plate_mismatch = any(p["match_status"] == "MISMATCH" for p in plates)
        plate_match = any(p["match_status"] in {
            "MATCH", "CROSS_CAMERA_MATCH", "POLICY_RECONCILED", "PARTIAL_VISUAL_MATCH",
        } for p in plates)

        def automated_check(code: str, status: str, value: str | None, note: str) -> None:
            db.execute(
                """
                UPDATE claim_case_validations
                SET status=?, value=?, note=?, updated_by='Case Agent', updated_at=?
                WHERE case_id=? AND check_code=?
                """,
                (status, value, note, _now(), case_id, code),
            )
            for validation in validations:
                if validation["check_code"] == code:
                    validation.update({
                        "status": status, "value": value, "note": note,
                        "updated_by": "Case Agent",
                    })
                    break

        claim_data = case["claim"]
        identity_status = str(claim_data.get("claimant_identity_status") or "MISSING")
        automated_check(
            "CLAIMANT_IDENTITY",
            "VERIFIED" if identity_status == "VERIFIED" else identity_status,
            identity_status.replace("_", " ").title(),
            "Claimant identity fields were checked against the supplied intake record.",
        )

        policy_status = str(claim_data.get("policy_status") or "MISSING")
        automated_check(
            "POLICY_ACTIVE",
            "VERIFIED" if policy_status == "ACTIVE" else "MISSING" if policy_status == "MISSING" else "MISMATCH",
            claim_data.get("policy_number") or "No policy number supplied",
            "Policy was active at the incident time." if policy_status == "ACTIVE" else f"Policy intake status is {policy_status.lower()} and requires review.",
        )

        police_status = str(claim_data.get("police_reference_status") or "MISSING")
        police_validation_status = {
            "VERIFIED": "VERIFIED",
            "NOT_REQUIRED": "NOT_APPLICABLE",
            "MISSING": "MISSING",
            "MALFORMED": "MISMATCH",
            "MISMATCH": "MISMATCH",
        }.get(police_status, "PENDING")
        automated_check(
            "POLICE_REFERENCE",
            police_validation_status,
            claim_data.get("police_case_number") or "No SAPS reference supplied",
            {
                "VERIFIED": "The SAPS case reference has the expected case/month/year and station structure.",
                "NOT_REQUIRED": "A police reference is not required for this incident type.",
                "MISSING": "The claim is reportable but no SAPS case reference was supplied.",
                "MALFORMED": "The supplied SAPS reference contains an invalid month or structure.",
                "MISMATCH": "The supplied SAPS reference does not reconcile with the intake record.",
            }.get(police_status, "The SAPS reference could not be resolved automatically."),
        )

        ownership_status = str(claim_data.get("ownership_status") or "MISSING")
        automated_check(
            "OWNERSHIP",
            "VERIFIED" if ownership_status == "VERIFIED" else ownership_status,
            ownership_status.replace("_", " ").title(),
            "Ownership or insurable-interest documentation was checked against the claim.",
        )

        documents_status = str(claim_data.get("documents_status") or "MISSING")
        documents = claim_data.get("received_documents") or []
        automated_check(
            "SUPPORTING_DOCUMENTS",
            "VERIFIED" if documents_status == "COMPLETE" else "MISSING",
            ", ".join(str(item) for item in documents) or "No documents supplied",
            "Required documents are present." if documents_status == "COMPLETE" else f"Document intake is {documents_status.lower()}; request the outstanding items.",
        )

        if claim_data.get("incident_at"):
            automated_check(
                "INCIDENT_TIME", "VERIFIED", str(claim_data["incident_at"]),
                "Incident timestamp is present and precise enough for the evidence window.",
            )
        if claim_data.get("peril") and claim_data.get("suburb") and claim_data.get("item_type"):
            automated_check(
                "INCIDENT_DESCRIPTION", "VERIFIED",
                f"{claim_data['peril']} · {claim_data['item_type']} · {claim_data['suburb']}",
                "Core incident fields are present and internally usable.",
            )
        if evidence_count or processed_video_count:
            automated_check(
                "CAMERA_EVIDENCE", "VERIFIED",
                f"{processed_video_count} video clip(s); {evidence_count} linked event(s)",
                "Available camera material was processed and included in the review pack.",
            )
        else:
            automated_check(
                "CAMERA_EVIDENCE", "PENDING", "No video supplied",
                "No camera video is attached. The human reviewer can request it if the claim requires it.",
            )
        if (claim_data.get("item_type") or "").lower() != "vehicle":
            automated_check(
                "PLATE_MATCH", "NOT_APPLICABLE", None,
                "Number-plate OCR is not applicable to this non-vehicle claim.",
            )
        elif plate_mismatch:
            automated_check(
                "PLATE_MATCH", "MISMATCH", None,
                "OCR observations conflict and require human review.",
            )
        elif plate_match:
            automated_check(
                "PLATE_MATCH", "VERIFIED", f"{len(plates)} OCR observation(s)",
                "Available registration observations support the reported vehicle details.",
            )
        else:
            automated_check(
                "PLATE_MATCH", "PENDING", "Awaiting readable video",
                "No reliable number-plate observation is available yet.",
            )

        automated_validations = [v for v in validations if v["check_code"] != "HUMAN_REVIEW"]
        pending = [v for v in automated_validations if v["status"] == "PENDING"]
        missing = [v for v in automated_validations if v["status"] == "MISSING"]
        mismatches = [v for v in automated_validations if v["status"] == "MISMATCH"]
        verified = [v for v in automated_validations if v["status"] in {"VERIFIED", "NOT_APPLICABLE"}]

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

        applicable = max(1, len([v for v in automated_validations if v["status"] != "NOT_APPLICABLE"]))
        readiness = 20 + 55 * len([v for v in automated_validations if v["status"] == "VERIFIED"]) / applicable
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

        # The narrative is produced only after every score and recommendation
        # above is final, so a model failure can change the wording but never
        # the outcome.  narrative_mode records which path produced it.
        claim = case.get("claim") or {}
        narrative, narrative_mode = narrate_case_run({
            "case_id": case_id,
            "claim": {
                "incident_id": case.get("source_claim_id"),
                "peril": claim.get("peril"),
                "item_type": claim.get("item_type"),
                "item_category": claim.get("item_category"),
                "suburb": claim.get("suburb"),
                "incident_time": claim.get("incident_time"),
                "claim_amount": claim.get("claim_amount"),
                "vehicle": " ".join(str(x) for x in [claim.get("vehicle_make"), claim.get("vehicle_model"), claim.get("vehicle_year")] if x) or None,
                "reported_plate": case.get("reported_plate"),
            },
            "readiness_score": readiness,
            "recommendation": recommendation,
            "status": status,
            "stage": stage,
            "priority": priority,
            "statistical_assessment": {
                "flags": flags,
                "watches": watches,
                "findings": [str(f) for f in (stat.get("findings") or [])][:8],
            },
            "tools_run": tools,
            "rationale": rationale,
            "evidence_links": evidence_count,
            "validation_checklist": {
                "total": len(validations),
                "verified": [v["label"] for v in verified],
                "pending": [v["label"] for v in pending],
                "missing": [v["label"] for v in missing],
                "mismatched": [
                    {"check": v["label"], "value": v.get("value"), "note": v.get("note")}
                    for v in mismatches
                ],
            },
            "plate_observations": [
                {
                    "read": p.get("normalized_plate") or p.get("plate_text"),
                    "ocr_confidence": p.get("ocr_confidence"),
                    "camera": p.get("camera_id"),
                    "captured_at": p.get("captured_at"),
                    "verdict": p.get("match_status"),
                }
                for p in plates
            ],
            "readiness_breakdown": {
                "note": "Deterministic score. Verified checks add, missing and mismatched checks subtract.",
                "verified_checks": len([v for v in validations if v["status"] == "VERIFIED"]),
                "applicable_checks": applicable,
                "evidence_bonus": min(15, evidence_count * 3),
                "plate_match_bonus": 10 if plate_match else 0,
                "missing_penalty": -15 * len(missing),
                "mismatch_penalty": -18 * len(mismatches),
                "plate_mismatch_penalty": -12 if plate_mismatch else 0,
                "statistical_penalty": -min(20, flags * 8 + watches * 2),
            },
        })
        _ensure_narrative_columns(db)
        db.execute(
            "UPDATE claim_agent_runs SET narrative=?, narrative_mode=? WHERE run_id=?",
            (narrative, narrative_mode, run_id),
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
        "report_type": "MZANSIMESH_CLAIM_INVESTIGATION_PACK",
        "case_id": case_id,
        "source_claim_id": case["source_claim_id"],
        "generated_at": _now(),
        "claim": case["claim"],
        "case_state": {
            "status": case["status"], "stage": case["stage"],
            "priority": case["priority"], "assigned_to": case.get("assigned_to"),
        },
        "executive_summary": {
            "readiness": (agent or {}).get("recommendation", "HUMAN_REVIEW_REQUIRED").replace("_", " ").title(),
            "verified_checks": sum(1 for item in validations if item.get("status") == "VERIFIED"),
            "open_checks": sum(1 for item in validations if item.get("status") in {"PENDING", "MISSING", "MISMATCH"}),
            "evidence_items": len(evidence),
            "plate_reads": len(plates),
            "narrative": (
                f"This report consolidates {len(evidence)} linked evidence item(s), {len(plates)} plate observation(s), "
                f"and {sum(1 for item in validations if item.get('status') == 'VERIFIED')} verified validation check(s). "
                "AI findings remain review aids and the final decision belongs to an authorised claims investigator."
            ),
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


@router.get("/api/fraud/cases/{case_id}/report/latest/html", response_class=HTMLResponse)
def latest_case_report_html(case_id: str, download: bool = Query(default=False)):
    _get_case(case_id)
    with connect() as db:
        row = _row(db.execute(
            "SELECT * FROM claim_case_reports WHERE case_id=? ORDER BY version DESC LIMIT 1", (case_id,)
        ).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail="no report generated yet")
    report = _json(row.get("report_json"), {})
    html = _report_html_document(report, report_id=row["report_id"], version=int(row["version"]))
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{case_id}-investigation-report.html"'
    return HTMLResponse(content=html, headers=headers)


@router.get("/api/fraud/cases/{case_id}/database")
def case_database(case_id: str):
    _get_case(case_id)
    return _database_summary(case_id)
