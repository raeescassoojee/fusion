from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from sentinel_ops.storage import connect

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeedbackCreate(BaseModel):
    category: Literal["GENERAL", "BUG", "FEATURE", "FALSE_MATCH", "NOTIFICATION"] = "GENERAL"
    rating: int = Field(default=5, ge=1, le=5)
    message: str = Field(min_length=3, max_length=1000)
    workspace: str = Field(default="member", max_length=40)
    actor: str = Field(default="Demo user", max_length=80)
    context: dict = Field(default_factory=dict)


def initialise_feedback_store() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                feedback_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                rating INTEGER NOT NULL,
                message TEXT NOT NULL,
                workspace TEXT NOT NULL,
                actor TEXT NOT NULL,
                context_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'NEW',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_created
                ON user_feedback(created_at DESC);
            """
        )


@router.post("")
def create_feedback(payload: FeedbackCreate):
    initialise_feedback_store()
    feedback_id = f"FDB-{uuid.uuid4().hex[:10].upper()}"
    created_at = _now()
    record = payload.model_dump()
    with connect() as db:
        db.execute(
            """
            INSERT INTO user_feedback(
                feedback_id, category, rating, message, workspace,
                actor, context_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
            """,
            (
                feedback_id,
                payload.category,
                payload.rating,
                payload.message.strip(),
                payload.workspace,
                payload.actor,
                json.dumps(payload.context, separators=(",", ":")),
                created_at,
            ),
        )
        # These shared tables exist after the Member store initialises. Keep this
        # best-effort so feedback never breaks the dashboard on a clean database.
        try:
            db.execute(
                """
                INSERT INTO member_audit_log(
                    audit_id, table_name, record_id, action, actor,
                    summary, payload_json, created_at
                ) VALUES (?, 'user_feedback', ?, 'CREATE', ?, ?, ?, ?)
                """,
                (
                    f"AUD-{uuid.uuid4().hex[:12].upper()}",
                    feedback_id,
                    payload.actor,
                    f"{payload.category} feedback rated {payload.rating}/5",
                    json.dumps(record, separators=(",", ":")),
                    created_at,
                ),
            )
            db.execute(
                """
                INSERT INTO aws_sync_outbox(
                    outbox_id, entity_type, entity_id, action,
                    payload_json, status, created_at
                ) VALUES (?, 'user_feedback', ?, 'CREATE', ?, 'LOCAL_PENDING', ?)
                """,
                (
                    f"OUT-{uuid.uuid4().hex[:12].upper()}",
                    feedback_id,
                    json.dumps({**record, "feedback_id": feedback_id, "created_at": created_at}, separators=(",", ":")),
                    created_at,
                ),
            )
        except Exception:
            pass
    return {
        "ok": True,
        "feedback_id": feedback_id,
        "created_at": created_at,
        "status": "NEW",
        "aws_sync": "LOCAL_PENDING",
    }


@router.get("")
def list_feedback(limit: int = Query(default=8, ge=1, le=50)):
    initialise_feedback_store()
    with connect() as db:
        rows = db.execute(
            """
            SELECT feedback_id, category, rating, message, workspace,
                   actor, status, created_at
            FROM user_feedback
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {"count": len(rows), "items": [dict(row) for row in rows]}
