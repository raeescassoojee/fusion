"""Case Narrator.

Takes the completed, deterministic output of ``run_case_agent`` and produces a
short investigator-readable narrative.

What the narrator is allowed to do:
  * describe which tools ran and what they returned
  * restate the rationale entries in plain language
  * name what a human should look at next

What the narrator must never do:
  * change the readiness score, the recommendation or the case status
  * assert an identity, a match or a person
  * introduce a fact that is not present in the trace it was given

Because the narrative is produced *after* every decision is final, a model
failure can only affect wording.  That is the whole reason this is safe to run
in front of an audience.
"""

from __future__ import annotations

from typing import Any

from .llm import generate_text

SYSTEM_PROMPT = """You write the case note an insurance claims investigator at a \
South African insurer reads first when opening a file.

You receive a JSON trace from an automated review that has ALREADY finished. Every \
score, recommendation and status is final and was produced by deterministic code.

OUTPUT FORMAT — follow exactly:
- Write one short line per point. Separate lines with a single newline.
- Do NOT use bullet characters, dashes, asterisks, numbering or headings. Plain lines only.
- First line: the claim itself — peril, vehicle or item, suburb, and amount as R 391 572.
- Then 3 to 5 further lines, each a single finding, ordered most to least important.
- Last line: the one next action the investigator should take.
- Each line is one sentence, under 22 words. No line may repeat another.

CONTENT RULES:
- Name specific checks by their label. Never write "3 checks are missing" without saying which.
- If plate observations exist, give the reading, the camera, and the verdict.
- If a mismatch exists, state plainly what does not line up.
- Give the readiness score with the single largest reason it is high or low.
- Report the recommendation exactly as given. Never soften or reinterpret it.

NEVER:
- State or imply a person has been identified, matched, or is a suspect.
- Suggest the claim is fraudulent. This assesses evidence completeness, not honesty.
- Introduce a fact absent from the trace, or mention a null field.
- Use hedging filler such as "it appears" or "it should be noted".

STYLE: British English, plain professional register, active voice."""


def _deterministic_narrative(trace: dict[str, Any]) -> str:
    """The fallback. One point per line, matching the model's output shape."""
    claim = trace.get("claim") or {}
    checks = trace.get("validation_checklist") or {}
    plates = trace.get("plate_observations") or []
    lines: list[str] = []

    peril = claim.get("peril")
    thing = claim.get("vehicle") or claim.get("item_type")
    lead = f"{peril} of {thing}" if peril and thing else (peril or thing or "Claim")
    if claim.get("suburb"):
        lead += f" in {claim['suburb']},"
    if claim.get("claim_amount"):
        try:
            lead += f" valued at R {float(claim['claim_amount']):,.0f}".replace(",", " ")
        except (TypeError, ValueError):
            pass
    lines.append(lead + ".")

    for m in (checks.get("mismatched") or [])[:2]:
        detail = f"Mismatch on {m.get('check')}"
        if m.get("value"):
            detail += f": {m['value']}"
        lines.append(detail + ".")

    for p in plates[:2]:
        if p.get("read"):
            verdict = str(p.get("verdict", "")).replace("_", " ").lower()
            cam = f" on {p['camera']}" if p.get("camera") else ""
            lines.append(f"Plate {p['read']} read{cam} — {verdict}." if verdict
                         else f"Plate {p['read']} read{cam}.")

    if checks.get("missing"):
        lines.append("Missing: " + "; ".join(checks["missing"][:3]) + ".")
    if checks.get("pending"):
        lines.append(f"{len(checks['pending'])} validation check(s) still pending.")
    if trace.get("evidence_links"):
        lines.append(f"{trace['evidence_links']} evidence item(s) linked and auditable.")

    rec = str(trace.get("recommendation", "")).replace("_", " ").lower()
    lines.append(f"Readiness {trace.get('readiness_score')}/100 — recommendation is {rec}.")
    return "\n".join(lines)


def narrate_case_run(trace: dict[str, Any]) -> tuple[str, str]:
    """Return ``(narrative, mode)`` for a completed agent run.

    ``mode`` is surfaced in the interface so the reader always knows whether
    they are looking at model-generated prose or the deterministic fallback.
    """
    fallback = _deterministic_narrative(trace)

    import json

    prompt = (
        "Case review trace:\n\n"
        + json.dumps(trace, ensure_ascii=False, indent=2, default=str)
        + "\n\nWrite the investigator case note."
    )

    text, mode = generate_text(SYSTEM_PROMPT, prompt)
    if text:
        return text, mode
    return fallback, mode
