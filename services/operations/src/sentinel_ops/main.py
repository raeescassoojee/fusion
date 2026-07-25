from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.aws_status import aws_status
from sentinel_ops.camera_bridge import camera_ai_to_operations
from sentinel_ops import activity
from sentinel_ops.community import announce_review
from sentinel_ops.camera_upload import router as camera_router
from sentinel_ops.patterns_api import router as patterns_router
from sentinel_ops.roles_api import router as roles_router
from sentinel_ops.member_mesh import router as member_mesh_router
from sentinel_ops.claims_case import router as claims_case_router, initialise_claim_store
from sentinel_ops.security_dispatch import router as security_dispatch_router, initialise_security_store
from sentinel_ops.claims_bridge import (
    build_metro_patrol,
    find_claims_file,
    list_metros,
    load_claims_hotspots,
)
from sentinel_ops.enrichment import fuse_operational_context
from sentinel_ops.evidence import compare_events
from sentinel_ops.integrated_demo import run_integrated_demo
from sentinel_ops.ingestion import ingest_claim, ingest_event
from sentinel_ops.models import (
    Alert,
    AlertEvaluationRequest,
    AlertReviewRequest,
    EnrichmentRequest,
    EvidenceComparisonRequest,
    EvidenceLink,
    IncidentTimeline,
    CameraEvent,
    Claim,
    PatrolComparison,
    PatrolRequest,
    ReconstructRequest,
)
from sentinel_ops.rewind import reconstruct_incident
from sentinel_ops.routing import optimise_patrol
from sentinel_ops.storage import (
    clear_all,
    get_alert,
    list_alerts,
    list_claims,
    list_events,
    save_alert,
    update_alert,
    status as storage_status,
)


OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = OPERATIONS_ROOT / "static"

app = FastAPI(
    title="Discovery Sentinel Mesh Operations API",
    version="0.2.0",
    description=(
        "Cassoojee claims bridge, evidence graph, reviewed alerts, incident rewind "
        "and fuel-aware patrol optimisation."
    ),
)

# Hackathon integration: allows a separately hosted local frontend to call the API.
# Lock this down to approved domains before production deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Camera intake (upload + roster) and evidence-pattern / height endpoints.
app.include_router(camera_router)
app.include_router(patterns_router)
app.include_router(roles_router)
app.include_router(member_mesh_router)
app.include_router(claims_case_router)
app.include_router(security_dispatch_router)


@app.on_event("startup")
def initialise_claim_workspace() -> None:
    initialise_claim_store()
    initialise_security_store()


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse(url="/dashboard")


# Record every API call on the live tape, so the console shows the request
# that caused a write alongside the write itself. Excludes the polling endpoint
# and static pages, which would otherwise flood the feed.
_TAPE_SKIP = ("/api/activity", "/console", "/dashboard", "/static", "/docs",
              "/redoc", "/openapi.json", "/favicon.ico")


@app.middleware("http")
async def log_api_calls(request, call_next):
    import time as _t
    started = _t.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api") and not path.startswith(_TAPE_SKIP):
        from sentinel_ops import activity as _activity
        _activity.record(
            action=request.method,
            backend="api",
            target=path,
            detail=f"{response.status_code}",
            status="ok" if response.status_code < 400 else "error",
            latency_ms=(_t.perf_counter() - started) * 1000,
        )
    return response


@app.get("/console", include_in_schema=False)
def console():
    """Live datastore tape - every write the system makes, in order."""
    return FileResponse(STATIC_DIR / "console.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/claims/metros")
def claims_metros():
    return {"metros": list_metros()}


@app.get("/api/claims/hotspots")
def claims_hotspots(metro: str | None = Query(default=None)):
    hotspots, metadata = load_claims_hotspots(metro)
    return {
        "metro": metro,
        "data_source": metadata,
        "hotspots": [hotspot.model_dump(mode="json") for hotspot in hotspots],
    }


@app.get("/api/claims/map", include_in_schema=False)
def claims_map():
    try:
        path, _ = find_claims_file("hotspots_map.html")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)


@app.get("/api/overview")
def overview(metro: str = Query(default="Gauteng")):
    try:
        return run_integrated_demo(metro)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/routes/metro/{metro}", response_model=PatrolComparison)
def route_for_metro(
    metro: str,
    fuel_l_per_100km: float = Query(default=10.0, gt=0),
    max_stops: int | None = Query(default=3, ge=1),
    pilot_radius_km: float | None = Query(default=None, gt=0),
):
    try:
        comparison, _, _, _ = build_metro_patrol(
            metro,
            fuel_l_per_100km=fuel_l_per_100km,
            max_stops=max_stops,
            pilot_radius_km=pilot_radius_km,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return comparison


@app.post("/api/evidence/compare", response_model=EvidenceLink)
def evidence_compare(request: EvidenceComparisonRequest):
    return compare_events(request.first, request.second)


@app.post("/api/alerts/evaluate", response_model=Alert)
def alert_evaluate(request: AlertEvaluationRequest):
    alert = evaluate_alert(request)
    if alert.status == "PENDING_REVIEW":
        save_alert(alert)
    return alert


@app.post("/api/alerts/{alert_id}/review", response_model=Alert)
def alert_review(alert_id: str, request: AlertReviewRequest):
    """Close the outcome loop: an operator accepts, dismisses or escalates a
    pending alert and the reason is stored for audit."""
    alert = get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown alert {alert_id}")
    if alert.status not in {"PENDING_REVIEW", "ACCEPTED", "DISMISSED", "ESCALATED"}:
        raise HTTPException(
            status_code=409, detail=f"Alert {alert_id} is not reviewable"
        )
    alert.status = request.decision
    alert.review_reason = request.reason
    alert.reviewed_by = request.reviewed_by
    alert.reviewed_at = datetime.now(timezone.utc)
    updated = update_alert(alert)

    # Best effort, privacy-safe community notice. Never blocks the review.
    if request.decision in {"ACCEPTED", "ESCALATED"} and alert.hotspot_id:
        try:
            hotspots, _ = load_claims_hotspots()
            match = next(
                (h for h in hotspots if h.hotspot_id == alert.hotspot_id), None
            )
            if match is not None:
                announce_review(
                    hotspot_name=match.name,
                    decision=request.decision,
                    peril=getattr(match, "main_peril", None),
                )
        except Exception:  # noqa: BLE001 - notice is optional
            pass

    return updated


@app.post("/api/cases/reconstruct", response_model=IncidentTimeline)
def case_reconstruct(request: ReconstructRequest):
    if request.events is None:
        request = request.model_copy(update={"events": list_events(limit=500)})
    return reconstruct_incident(request)


@app.post("/api/routes/optimise", response_model=PatrolComparison)
def route_optimise(request: PatrolRequest):
    return optimise_patrol(request)


@app.post("/api/enrichment/fuse")
def enrichment_fuse(request: EnrichmentRequest):
    return fuse_operational_context(request)


@app.get("/api/storage/status")
def get_storage_status():
    return storage_status()


@app.get("/api/events")
def get_events(limit: int = Query(default=100, ge=1, le=1000)):
    return [event.model_dump(mode="json") for event in list_events(limit)]


@app.post("/api/events")
def post_event(event: CameraEvent):
    return ingest_event(event)


@app.post("/api/events/camera-ai")
def post_camera_ai_event(payload: dict):
    """Accept Person 3's event-v1 JSON and translate it into the operations contract."""
    try:
        event = camera_ai_to_operations(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid camera-ai event: {exc}") from exc
    return ingest_event(event)


@app.get("/api/claims")
def get_claims(limit: int = Query(default=100, ge=1, le=1000)):
    return [claim.model_dump(mode="json") for claim in list_claims(limit)]


@app.post("/api/claims")
def post_claim(claim: Claim):
    return ingest_claim(claim)


@app.get("/api/alerts")
def get_alerts(limit: int = Query(default=100, ge=1, le=1000)):
    return [alert.model_dump(mode="json") for alert in list_alerts(limit)]


@app.post("/api/demo/seed")
def seed_demo():
    """Seed the runtime SQLite store with the bundled synthetic camera events and claim."""
    import json

    fixtures = OPERATIONS_ROOT / "fixtures"
    events_payload = json.loads((fixtures / "events.json").read_text(encoding="utf-8"))
    claim_payload = json.loads((fixtures / "claim.json").read_text(encoding="utf-8"))
    event_results = [ingest_event(CameraEvent.model_validate(item)) for item in events_payload]
    claim_result = ingest_claim(Claim.model_validate(claim_payload))
    return {
        "seeded_events": len(event_results),
        "claim_id": claim_result["claim"]["claim_id"],
        "storage": storage_status(),
        "notice": "Synthetic/consented demo fixtures only.",
    }


@app.delete("/api/demo/reset")
def reset_demo():
    """Reset the local operational store between judging runs."""
    removed = clear_all()
    return {"removed": removed, "storage": storage_status()}


@app.get("/api/activity")
def activity_feed(since: int = 0, limit: int = 100):
    """Live datastore activity for the dashboard feed. Poll with the last
    latest_seq you received to get only new rows."""
    from sentinel_ops.activity import since as activity_since
    return activity_since(seq=since, limit=limit)


@app.get("/api/aws/status")
def get_aws_status():
    return aws_status()
