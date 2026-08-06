from __future__ import annotations

import itertools
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from sentinel_ops.alerts import evaluate_alert
from sentinel_ops.aws_status import aws_status
from sentinel_ops.camera_bridge import camera_ai_to_operations
from sentinel_ops.camera_upload import router as camera_router
from sentinel_ops.claims_bridge import (
    build_metro_patrol,
    find_claims_file,
    list_metros,
    load_claims_hotspots,
)
from sentinel_ops.claims_case import (
    clear_claim_case_demo_data,
    initialise_claim_store,
)
from sentinel_ops.claims_case import (
    router as claims_case_router,
)
from sentinel_ops.community_api import (
    initialise_community_store,
    reset_community_demo,
)
from sentinel_ops.community_api import (
    router as community_router,
)
from sentinel_ops.enrichment import fuse_operational_context
from sentinel_ops.evidence import compare_events
from sentinel_ops.evidence_passport import router as evidence_passport_router
from sentinel_ops.feedback_api import initialise_feedback_store
from sentinel_ops.feedback_api import router as feedback_router
from sentinel_ops.ingestion import ingest_claim, ingest_event
from sentinel_ops.integrated_demo import run_integrated_demo
from sentinel_ops.member_mesh import clear_member_demo_data
from sentinel_ops.member_mesh import router as member_mesh_router
from sentinel_ops.models import (
    Alert,
    AlertEvaluationRequest,
    CameraEvent,
    Claim,
    EnrichmentRequest,
    EvidenceComparisonRequest,
    EvidenceLink,
    IncidentTimeline,
    PatrolComparison,
    PatrolRequest,
    ReconstructRequest,
)
from sentinel_ops.patterns_api import router as patterns_router
from sentinel_ops.performance import (
    ClientMetric,
    performance_snapshot,
    record_client_metric,
    reset_metrics,
)
from sentinel_ops.rewind import reconstruct_incident
from sentinel_ops.roles_api import router as roles_router
from sentinel_ops.routing import optimise_patrol
from sentinel_ops.security_dispatch import (
    initialise_security_store,
    reset_security_demo,
)
from sentinel_ops.security_dispatch import (
    router as security_dispatch_router,
)
from sentinel_ops.storage import (
    clear_all,
    list_alerts,
    list_claims,
    list_events,
)
from sentinel_ops.storage import (
    status as storage_status,
)

OPERATIONS_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = OPERATIONS_ROOT / "static"

app = FastAPI(
    title="MzansiMesh Operations API",
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


# --- request feed -------------------------------------------------------------
# A rolling record of every HTTP request this process has served, so the activity
# console can show real traffic from the dashboard rather than checks it made
# itself. Held in memory only; it resets when the application restarts.
REQUEST_LOG: deque[dict[str, Any]] = deque(maxlen=600)
_REQUEST_SEQ = itertools.count(1)
_FEED_PATH = "/api/requests"


def _record(request: Request, status: int, started: float) -> None:
    REQUEST_LOG.append(
        {
            "seq": next(_REQUEST_SEQ),
            "at": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "client": request.client.host if request.client else "-",
        }
    )


@app.middleware("http")
async def record_request(request: Request, call_next):
    """Record method, path, status and duration for the activity console."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Record the failure before re-raising, otherwise a 500 never reaches the
        # feed and the console shows a gap with no explanation.
        _record(request, 500, started)
        raise
    # The console polls the feed itself; logging that would drown out real traffic.
    if request.url.path != _FEED_PATH:
        _record(request, response.status_code, started)
    return response


@app.get(_FEED_PATH, include_in_schema=False)
def request_feed(after: int = 0, limit: int = 200):
    """Return requests newer than ``after``. The activity console polls this."""
    items = [entry for entry in REQUEST_LOG if entry["seq"] > after][-limit:]
    return {
        "requests": items,
        "latest_seq": REQUEST_LOG[-1]["seq"] if REQUEST_LOG else 0,
        "captured": len(REQUEST_LOG),
    }


@app.middleware("http")
async def prevent_stale_api_reads(request: Request, call_next):
    """Live judging screens must never reuse a pre-sighting API response."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# Camera intake (upload + roster) and evidence-pattern / height endpoints.
app.include_router(camera_router)
app.include_router(patterns_router)
app.include_router(roles_router)
app.include_router(member_mesh_router)
app.include_router(claims_case_router)
app.include_router(security_dispatch_router)
app.include_router(feedback_router)
app.include_router(community_router)
app.include_router(evidence_passport_router)


@app.on_event("startup")
def initialise_claim_workspace() -> None:
    initialise_claim_store()
    initialise_security_store()
    initialise_feedback_store()
    initialise_community_store()


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse(url="/dashboard")


@app.get("/console", include_in_schema=False)
def console():
    """Live activity console: real requests against this deployment."""
    return FileResponse(STATIC_DIR / "console.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/api/performance")
def get_performance():
    return performance_snapshot()


@app.delete("/api/performance")
def clear_performance():
    reset_metrics()
    return performance_snapshot()


@app.post("/api/performance/record")
def post_performance_metric(metric: ClientMetric):
    """Record a measured browser round trip from a small, approved metric set."""
    try:
        record_client_metric(metric)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"recorded": True, "name": metric.name, "milliseconds": metric.milliseconds}


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
    return evaluate_alert(request)


@app.post("/api/cases/reconstruct", response_model=IncidentTimeline)
def case_reconstruct(request: ReconstructRequest):
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
    """Seed the local operational store with bundled synthetic/consented fixtures."""
    import json

    fixtures = OPERATIONS_ROOT / "fixtures"
    events_payload = json.loads((fixtures / "events.json").read_text(encoding="utf-8"))
    claim_payload = json.loads((fixtures / "claim.json").read_text(encoding="utf-8"))
    event_results = [
        ingest_event(CameraEvent.model_validate(item)) for item in events_payload
    ]
    claim_result = ingest_claim(Claim.model_validate(claim_payload))
    return {
        "seeded_events": len(event_results),
        "claim_id": claim_result["claim"]["claim_id"],
        "storage": storage_status(),
        "notice": "Synthetic/consented demo fixtures only.",
    }


@app.delete("/api/demo/reset")
def reset_demo(
    full: bool = Query(default=True),
    reseed: bool = Query(default=False),
):
    """Idempotently reset all transient judging state.

    ``reseed=false`` preserves the original API's clean-store behaviour.  The
    Reset Demo button can use ``reseed=true`` to restore bundled operational
    fixtures immediately after clearing the member, claims and patrol stories.
    """
    removed: dict[str, object] = {"operational": clear_all()}
    if full:
        removed["claims_workspace"] = clear_claim_case_demo_data()
        removed["security"] = reset_security_demo()
        removed["community"] = reset_community_demo()
        removed["member_mesh"] = clear_member_demo_data(reset_cameras=True)
    reset_metrics()
    seeded = seed_demo() if reseed else None
    return {
        "reset": True,
        "full": full,
        "reseeded": bool(reseed),
        "removed": removed,
        "seed": seeded,
        "storage": storage_status(),
        "performance": performance_snapshot(),
    }


@app.get("/api/agent/status")
def agent_status_endpoint():
    """Report agentic-AI configuration truthfully, like /api/aws/status."""
    from sentinel_ops.agent.llm import agent_configuration

    return agent_configuration()


@app.get("/api/aws/status")
def get_aws_status():
    """Report whether the optional S3/DynamoDB integration is ready."""
    return aws_status()
