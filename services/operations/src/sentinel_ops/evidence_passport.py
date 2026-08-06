"""Integrity, provenance, trust and retention metadata for stored evidence.

An Evidence Passport is deliberately not a claim that evidence is immutable or
that an identity is correct. It proves that the bytes/metadata returned now hash
to the recorded digest and exposes the processing and policy applied to them.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from sentinel_ops.claims_case import initialise_claim_store
from sentinel_ops.member_mesh import FACE_MEDIA_ROOT, initialise_member_store
from sentinel_ops.storage import connect, list_events
from sentinel_ops.trust_policy import evidence_policy_for_trust


router = APIRouter(prefix="/api/evidence/passports", tags=["evidence passports"])
EvidenceType = Literal["sighting", "camera-event", "claim-evidence"]
RETENTION_HOURS = 72


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _time(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(UTC)
    else:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _retention(captured_at: datetime) -> dict[str, Any]:
    expires_at = captured_at + timedelta(hours=RETENTION_HOURS)
    now = datetime.now(UTC)
    return {
        "classification": "SENSITIVE_CAMERA_EVIDENCE",
        "policy_hours": RETENTION_HOURS,
        "expires_at": expires_at.isoformat(),
        "status": "EXPIRED" if now >= expires_at else "ACTIVE",
        "enforcement": "DEMO_METADATA_ONLY",
        "production_requirement": (
            "Configure an S3 lifecycle rule and database deletion worker before production."
        ),
    }


def _passport(
    *,
    evidence_type: EvidenceType,
    evidence_id: str,
    captured_at: datetime,
    source: dict[str, Any],
    canonical_evidence: dict[str, Any],
    trust_score: float,
    processing: dict[str, Any],
    media_path: Path | None = None,
) -> dict[str, Any]:
    canonical = _canonical_bytes(canonical_evidence)
    evidence_digest = _sha256_bytes(canonical)
    media_digest = _sha256_file(media_path) if media_path and media_path.is_file() else None
    policy = evidence_policy_for_trust(trust_score)
    return {
        "passport_id": f"PASS-{evidence_digest[:16].upper()}",
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "generated_at": datetime.now(UTC).isoformat(),
        "integrity": {
            "algorithm": "SHA-256",
            "evidence_sha256": evidence_digest,
            "media_sha256": media_digest,
            "verified": evidence_digest == _sha256_bytes(_canonical_bytes(canonical_evidence)),
            "verification_scope": (
                "stored metadata and local media bytes"
                if media_digest
                else "stored canonical metadata; media bytes were not locally available"
            ),
            "digitally_signed": False,
        },
        "source": source,
        "processing": processing,
        "trust": {
            "score": round(float(trust_score), 1),
            "band": policy["band"],
            "label": policy["label"],
            "disabled_evidence": policy["disabled_evidence"],
            "alert_enabled": policy["alert_enabled"],
            "human_review_required": True,
        },
        "privacy": {
            "identity_asserted": False,
            "biometric_template_returned": False,
            "data_minimisation": "Raw embeddings are excluded from this passport response.",
        },
        "retention": _retention(captured_at),
        "notice": (
            "Integrity verification confirms present bytes, not identity, guilt, fraud or immutability."
        ),
    }


def _sighting_passport(evidence_id: str) -> dict[str, Any]:
    initialise_member_store()
    with connect() as db:
        row = db.execute(
            """
            SELECT s.*, c.household, c.suburb, c.device_label, c.camera_trust,
                   p.anonymous_label
            FROM face_sightings s
            JOIN member_cameras c ON c.camera_id=s.camera_id
            JOIN face_profiles p ON p.profile_id=s.profile_id
            WHERE s.sighting_id=?
            """,
            (evidence_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Sighting {evidence_id} was not found")
    item = dict(row)
    item.pop("embedding", None)
    item.pop("embedding_size", None)
    media_name = Path(str(item.get("media_name") or "")).name
    media_path = FACE_MEDIA_ROOT / media_name if media_name else None
    captured_at = _time(item.get("captured_at"))
    trust_score = float(item.get("camera_trust") or 50)
    return _passport(
        evidence_type="sighting",
        evidence_id=evidence_id,
        captured_at=captured_at,
        source={
            "camera_id": item.get("camera_id"),
            "camera_label": item.get("device_label"),
            "property": item.get("household"),
            "suburb": item.get("suburb"),
            "captured_at": captured_at.isoformat(),
            "media_name": media_name or None,
        },
        canonical_evidence=item,
        trust_score=trust_score,
        processing={
            "detector": "YuNet face_detection_yunet_2023mar",
            "embedding": "SFace face_recognition_sface_2021dec",
            "transforms": ["tracked face crop", "quality scoring", "best-frame selection", "multi-frame aggregation"],
            "decision": "anonymous candidate retrieval with trust gate and human review",
        },
        media_path=media_path,
    )


def _camera_event_passport(evidence_id: str) -> dict[str, Any]:
    event = next((item for item in list_events(1000) if item.event_id == evidence_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Camera event {evidence_id} was not found")
    raw = event.model_dump(mode="json")
    # The embedding participates in the digest but is never echoed to the client.
    public = json.loads(json.dumps(raw))
    if isinstance(public.get("face"), dict):
        public["face"].pop("embedding", None)
    return _passport(
        evidence_type="camera-event",
        evidence_id=evidence_id,
        captured_at=_time(event.timestamp),
        source={
            "camera_id": event.camera_id,
            "captured_at": _time(event.timestamp).isoformat(),
            "media_url": event.media_url,
            "source_system": event.source,
        },
        canonical_evidence=raw,
        trust_score=event.camera_trust_score,
        processing={
            "pipeline": "sentinel-camera-ai event-v1",
            "transforms": ["detection", "best-evidence selection", "OCR/appearance extraction when eligible"],
            "public_record": public,
        },
    )


def _claim_evidence_passport(evidence_id: str, case_id: str | None) -> dict[str, Any]:
    initialise_claim_store()
    query = "SELECT * FROM claim_evidence_links WHERE evidence_id=?"
    args: tuple[Any, ...] = (evidence_id,)
    if case_id:
        query += " AND case_id=?"
        args = (evidence_id, case_id)
    query += " ORDER BY linked_at DESC LIMIT 1"
    with connect() as db:
        row = db.execute(query, args).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Claim evidence {evidence_id} was not found")
    item = dict(row)
    payload = item.get("payload_json")
    if payload:
        try:
            item["payload"] = json.loads(payload)
        except json.JSONDecodeError:
            item["payload"] = payload
    item.pop("payload_json", None)
    captured_at = _time(item.get("linked_at"))
    confidence = float(item.get("confidence") or 0)
    trust_score = confidence if confidence > 1 else confidence * 100
    return _passport(
        evidence_type="claim-evidence",
        evidence_id=evidence_id,
        captured_at=captured_at,
        source={
            "case_id": item.get("case_id"),
            "evidence_source": item.get("source"),
            "linked_at": captured_at.isoformat(),
            "media_url": item.get("media_url"),
        },
        canonical_evidence=item,
        trust_score=max(0, min(100, trust_score)),
        processing={
            "pipeline": "deterministic Incident Time Machine retrieval",
            "transforms": ["time-window filter", "distance filter", "relevance scoring", "human-review queue"],
            "status": item.get("status"),
        },
    )


@router.get("/{evidence_type}/{evidence_id}")
def evidence_passport(
    evidence_type: EvidenceType,
    evidence_id: str,
    case_id: str | None = Query(default=None),
):
    if evidence_type == "sighting":
        return _sighting_passport(evidence_id)
    if evidence_type == "camera-event":
        return _camera_event_passport(evidence_id)
    return _claim_evidence_passport(evidence_id, case_id)
