from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from sentinel_ops.storage import connect

router = APIRouter(prefix="/api/community", tags=["community"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CommunityMessageCreate(BaseModel):
    channel_id: str = Field(default="lakefield", min_length=2, max_length=80)
    author: str = Field(default="Community member", min_length=1, max_length=100)
    role: Literal["Resident", "Security", "Staff", "System"] = "Resident"
    message: str = Field(min_length=1, max_length=600)
    urgent: bool = False
    source: str = Field(default="USER", max_length=40)


def _ensure_table(db) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS community_messages (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            author TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            urgent INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'USER',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_community_channel_created
            ON community_messages(channel_id, created_at DESC);
        """
    )


def _insert_message(
    db,
    *,
    channel_id: str,
    author: str,
    role: str,
    message: str,
    urgent: bool = False,
    source: str = "USER",
    message_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    _ensure_table(db)
    item = {
        "message_id": message_id or f"MSG-{uuid.uuid4().hex[:12].upper()}",
        "channel_id": channel_id.strip().lower() or "lakefield",
        "author": author.strip() or "MzansiMesh",
        "role": role.strip() or "System",
        "message": message.strip(),
        "urgent": bool(urgent),
        "source": source.strip().upper() or "SYSTEM",
        "created_at": created_at or _now(),
    }
    db.execute(
        """
        INSERT OR IGNORE INTO community_messages(
            message_id, channel_id, author, role, message,
            urgent, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["message_id"], item["channel_id"], item["author"], item["role"],
            item["message"], int(item["urgent"]), item["source"], item["created_at"],
        ),
    )
    return item


def record_community_system_message(
    db,
    *,
    message: str,
    channel_id: str = "lakefield",
    urgent: bool = False,
    source: str = "SYSTEM",
    dedupe_key: str | None = None,
) -> dict:
    """Write a community/system update using an existing transaction.

    A deterministic ID may be supplied through ``dedupe_key`` so repeated polling
    or retries do not create duplicate messages.
    """
    message_id = f"SYS-{dedupe_key}" if dedupe_key else None
    return _insert_message(
        db,
        channel_id=channel_id,
        author="MzansiMesh",
        role="System",
        message=message,
        urgent=urgent,
        source=source,
        message_id=message_id,
    )


def initialise_community_store() -> None:
    with connect() as db:
        _ensure_table(db)
        count = db.execute("SELECT COUNT(*) AS n FROM community_messages WHERE channel_id='lakefield'").fetchone()["n"]
        if count == 0:
            seed = [
                ("SEED-LAKEFIELD-1", "Lakefield Response", "Security", "Evening patrol coverage is active around Lakefield and Northmead.", 0, "SEED"),
                ("SEED-LAKEFIELD-2", "Community member", "Resident", "Ness Avenue cameras are online and sharing reviewed incident events.", 0, "SEED"),
                ("SEED-LAKEFIELD-3", "MzansiMesh", "Staff", "Confirmed incident matches will alert neighbouring households and the security control room.", 0, "SEED"),
            ]
            base = datetime.now(UTC)
            for idx, (message_id, author, role, message, urgent, source) in enumerate(seed):
                created = base.replace(microsecond=0).isoformat()
                _insert_message(
                    db,
                    channel_id="lakefield",
                    author=author,
                    role=role,
                    message=message,
                    urgent=bool(urgent),
                    source=source,
                    message_id=message_id,
                    created_at=created,
                )


def reset_community_demo() -> dict[str, int]:
    """Remove live/user messages while retaining the deterministic seed story."""
    initialise_community_store()
    with connect() as db:
        removed = int(
            db.execute(
                "SELECT COUNT(*) AS n FROM community_messages WHERE source<>'SEED'"
            ).fetchone()["n"]
        )
        db.execute("DELETE FROM community_messages WHERE source<>'SEED'")
    return {"community_messages": removed}


@router.get("/messages")
def list_messages(
    channel_id: str = Query(default="lakefield", min_length=2, max_length=80),
    limit: int = Query(default=60, ge=1, le=200),
):
    initialise_community_store()
    with connect() as db:
        rows = db.execute(
            """
            SELECT message_id, channel_id, author, role, message,
                   urgent, source, created_at
            FROM community_messages
            WHERE channel_id=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel_id.strip().lower(), limit),
        ).fetchall()
    items = [dict(row) for row in reversed(rows)]
    for item in items:
        item["urgent"] = bool(item["urgent"])
    return {"channel_id": channel_id.strip().lower(), "count": len(items), "items": items}


@router.post("/messages")
def create_message(payload: CommunityMessageCreate):
    initialise_community_store()
    with connect() as db:
        item = _insert_message(
            db,
            channel_id=payload.channel_id,
            author=payload.author,
            role=payload.role,
            message=payload.message,
            urgent=payload.urgent,
            source=payload.source,
        )
        try:
            db.execute(
                """
                INSERT INTO member_audit_log(
                    audit_id, table_name, record_id, action, actor,
                    summary, payload_json, created_at
                ) VALUES (?, 'community_messages', ?, 'CREATE', ?, ?, ?, ?)
                """,
                (
                    f"AUD-{uuid.uuid4().hex[:12].upper()}",
                    item["message_id"],
                    item["author"],
                    f"Community message posted in {item['channel_id']}",
                    json.dumps(item, separators=(",", ":")),
                    item["created_at"],
                ),
            )
            db.execute(
                """
                INSERT INTO aws_sync_outbox(
                    outbox_id, entity_type, entity_id, action,
                    payload_json, status, created_at
                ) VALUES (?, 'community_messages', ?, 'CREATE', ?, 'LOCAL_PENDING', ?)
                """,
                (
                    f"OUT-{uuid.uuid4().hex[:12].upper()}",
                    item["message_id"],
                    json.dumps(item, separators=(",", ":")),
                    item["created_at"],
                ),
            )
        except Exception:
            pass
    return {"ok": True, **item, "aws_sync": "LOCAL_PENDING"}
