"""Bridge from operations to the community chat service.

Posts a privacy-safe notice into a suburb's community group when an operator
accepts or escalates an alert. Deliberately one-directional and deliberately
identity-free: no plate text, no face similarity, no evidence crop and no
event id ever crosses into the community layer.

Fire-and-forget by design. If the chat service is not running the review still
succeeds - the notice is best effort, never a dependency.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

CHAT_URL = os.getenv("SENTINEL_CHAT_URL", "http://localhost:8080")
SYSTEM_KEY = os.getenv("SENTINEL_SYSTEM_KEY", "")
TIMEOUT_SECONDS = 2.0

# Must match createGroupId() in services/chat/server.js
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def group_id_for(name: str) -> str:
    """Slugify a hotspot name the same way the chat server does."""
    slug = _SLUG_STRIP.sub("-", str(name or "").lower())
    return slug.strip("-")[:40]


def _peril_for(claim_type: str | None) -> str:
    """Map a claims peril onto the chat service's accepted perils."""
    value = (claim_type or "").strip().lower()
    if "invasion" in value or "burglar" in value:
        return "Home Invasion"
    if "vehicle" in value or "theft" in value:
        return "Vehicle Theft"
    return "Suspicious Activity"


def announce_review(
    hotspot_name: str,
    decision: str,
    peril: str | None = None,
    suburb_label: str | None = None,
) -> dict:
    """Post a notice about a reviewed alert. Returns a small status dict.

    Never raises: a chat outage must not break the review endpoint.
    """
    if not SYSTEM_KEY:
        return {"sent": False, "reason": "SENTINEL_SYSTEM_KEY not set"}

    group_id = group_id_for(hotspot_name)
    if not group_id:
        return {"sent": False, "reason": "no group for hotspot"}

    if decision == "ESCALATED":
        text = (
            "Security has escalated a reviewed alert in this area and a patrol "
            "is being dispatched. No personal or identifying details are shared here."
        )
    elif decision == "ACCEPTED":
        text = (
            "Security reviewed an alert in this area and confirmed it is worth "
            "attention. No personal or identifying details are shared here."
        )
    else:
        return {"sent": False, "reason": f"no notice for decision {decision}"}

    payload = json.dumps(
        {
            "groupId": group_id,
            "peril": _peril_for(peril),
            "location": suburb_label or hotspot_name,
            "text": text,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{CHAT_URL}/system/announce",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-sentinel-key": SYSTEM_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
        return {"sent": True, "group_id": group_id, "message_id": body.get("messageId")}
    except urllib.error.HTTPError as exc:
        return {"sent": False, "reason": f"chat returned {exc.code}", "group_id": group_id}
    except Exception as exc:  # noqa: BLE001 - never break the review
        return {"sent": False, "reason": f"chat unreachable: {type(exc).__name__}"}
