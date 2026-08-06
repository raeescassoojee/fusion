"""Language-model client for the case narrator.

Provider-agnostic by design: ``narrator.py`` only calls ``generate_text`` and
receives ``(text, mode)`` back, so swapping providers never touches the caller.

Design rules:
  * Every call is bounded by a hard timeout. A slow model must never hold a
    request open during a live demonstration.
  * Every failure returns ``None`` rather than raising, so the caller falls
    back to deterministic output.
  * ``SENTINEL_AGENT_MODE=offline`` disables the model entirely. Set it,
    restart, and the system behaves exactly as it did before the agent existed.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

AGENT_MODE = os.getenv("SENTINEL_AGENT_MODE", "auto").strip().lower()
AGENT_PROVIDER = os.getenv("SENTINEL_AGENT_PROVIDER", "gemini").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("SENTINEL_AGENT_MODEL", "gemini-2.0-flash").strip()

try:
    AGENT_TIMEOUT_SECONDS = max(2.0, float(os.getenv("SENTINEL_AGENT_TIMEOUT", "12")))
except ValueError:
    AGENT_TIMEOUT_SECONDS = 12.0

try:
    AGENT_MAX_TOKENS = max(128, int(os.getenv("SENTINEL_AGENT_MAX_TOKENS", "900")))
except ValueError:
    AGENT_MAX_TOKENS = 600


def agent_enabled() -> bool:
    """True when a model call should be attempted at all."""
    if AGENT_MODE == "offline":
        return False
    return bool(GEMINI_API_KEY)


def agent_configuration() -> dict[str, Any]:
    """Report configuration truthfully, in the style of ``aws_status``."""
    missing = [] if GEMINI_API_KEY else ["GEMINI_API_KEY"]
    return {
        "enabled": agent_enabled(),
        "mode": AGENT_MODE,
        "provider": "Google Gemini",
        "model": GEMINI_MODEL,
        "timeout_seconds": AGENT_TIMEOUT_SECONDS,
        "max_tokens": AGENT_MAX_TOKENS,
        "missing": missing,
    }


def _call_gemini(system_prompt: str, user_prompt: str, out: dict[str, Any]) -> None:
    """Run the HTTP call. Executed on a worker thread so it can be abandoned."""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": AGENT_MAX_TOKENS,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=AGENT_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        candidates = payload.get("candidates") or []
        if not candidates:
            out["error"] = "NoCandidates"
            return
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
        if text:
            out["text"] = text
        else:
            out["error"] = "EmptyResponse"
    except urllib.error.HTTPError as exc:
        out["error"] = f"HTTP{exc.code}"
    except urllib.error.URLError:
        out["error"] = "NetworkUnreachable"
    except Exception as exc:
        out["error"] = type(exc).__name__


def generate_text(system_prompt: str, user_prompt: str) -> tuple[str | None, str]:
    """Return ``(text, mode)``.

    ``mode`` is ``AGENTIC``, ``DISABLED``, ``TIMEOUT`` or ``ERROR:<detail>``.
    The caller uses it to label output honestly in the interface, so a fallback
    summary is never presented as though a model produced it.
    """
    if not agent_enabled():
        return None, "DISABLED"

    out: dict[str, Any] = {}
    worker = threading.Thread(
        target=_call_gemini, args=(system_prompt, user_prompt, out), daemon=True
    )
    worker.start()
    worker.join(timeout=AGENT_TIMEOUT_SECONDS + 1.0)

    if worker.is_alive():
        return None, "TIMEOUT"
    if "text" in out:
        return out["text"], "AGENTIC"
    return None, f"ERROR:{out.get('error', 'Unknown')}"
