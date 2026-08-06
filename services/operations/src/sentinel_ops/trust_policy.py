"""One explainable evidence policy shared by alerts, linking and the live UI."""
from __future__ import annotations

from typing import Any


def evidence_policy_for_trust(score: float | int) -> dict[str, Any]:
    """Return the P1 trust gate for a 0-100 camera/evidence score.

    The policy never deletes raw evidence.  It controls which derived operations
    are allowed, so a low-trust event remains auditable without becoming an alert
    or biometric assertion.
    """
    trust = max(0.0, min(100.0, float(score)))
    if trust >= 85:
        band = "STRONG"
        label = "All evidence enabled"
        height_enabled = True
        biometric_escalation_enabled = True
        alert_enabled = True
        metadata_only = False
        disabled: list[str] = []
    elif trust >= 70:
        band = "USABLE"
        label = "Height disabled"
        height_enabled = False
        biometric_escalation_enabled = True
        alert_enabled = True
        metadata_only = False
        disabled = ["HEIGHT_ESTIMATION"]
    elif trust >= 50:
        band = "WEAK"
        label = "Biometric escalation disabled"
        height_enabled = False
        biometric_escalation_enabled = False
        alert_enabled = True
        metadata_only = False
        disabled = ["HEIGHT_ESTIMATION", "BIOMETRIC_ESCALATION"]
    else:
        band = "METADATA_ONLY"
        label = "Metadata only — alerts blocked"
        height_enabled = False
        biometric_escalation_enabled = False
        alert_enabled = False
        metadata_only = True
        disabled = ["HEIGHT_ESTIMATION", "BIOMETRIC_ESCALATION", "ALERT_ESCALATION"]
    return {
        "trust_score": round(trust, 1),
        "band": band,
        "label": label,
        "height_enabled": height_enabled,
        "biometric_escalation_enabled": biometric_escalation_enabled,
        "alert_enabled": alert_enabled,
        "metadata_only": metadata_only,
        "disabled_evidence": disabled,
        "raw_evidence_retained": True,
        "human_review_required": True,
    }
