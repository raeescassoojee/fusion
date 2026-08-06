from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PeakWindow(BaseModel):
    days: list[str] = Field(default_factory=list)
    start: str = "00:00"
    end: str = "23:59"


class PlateSignal(BaseModel):
    text: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class FaceSignal(BaseModel):
    reference_token: str | None = None
    embedding: list[float] | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class VehicleSignal(BaseModel):
    colour: str | None = None
    type: str | None = None
    make: str | None = None
    model: str | None = None


class AppearanceSignal(BaseModel):
    upper_colour: str | None = None
    lower_colour: str | None = None
    descriptor_token: str | None = None


class CameraEvent(BaseModel):
    event_id: str
    camera_id: str
    timestamp: datetime
    location: Location
    media_url: str | None = None
    plate: PlateSignal = Field(default_factory=PlateSignal)
    face: FaceSignal = Field(default_factory=FaceSignal)
    vehicle: VehicleSignal = Field(default_factory=VehicleSignal)
    appearance: AppearanceSignal = Field(default_factory=AppearanceSignal)
    camera_trust_score: float = Field(default=50.0, ge=0, le=100)
    evidence_policy: dict = Field(default_factory=dict)
    source: str = "demo"


class Hotspot(BaseModel):
    hotspot_id: str
    name: str
    location: Location
    risk_score: float = Field(ge=0, le=100)
    claims_risk_score: float | None = Field(default=None, ge=0, le=100)
    main_peril: str | None = None
    peak_window: PeakWindow = Field(default_factory=PeakWindow)
    geofence_radius_km: float = Field(default=1.5, gt=0)
    saps_context_score: float | None = Field(default=None, ge=0, le=100)
    camera_coverage_score: float | None = Field(default=None, ge=0, le=100)
    response_success_score: float | None = Field(default=None, ge=0, le=100)
    operational_priority: float | None = Field(default=None, ge=0, le=100)
    metro: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=100)
    confidence_band: str | None = None
    claim_count: int | None = Field(default=None, ge=0)
    total_claim_value: float | None = Field(default=None, ge=0)
    primary_driver: str | None = None
    saps_year: str | None = None
    saps_corroborated: bool | None = None
    source: str = "operations-fixture"


class Claim(BaseModel):
    claim_id: str
    incident_time: datetime
    location: Location
    claim_type: str
    claim_amount: float = Field(default=0, ge=0)
    plate_text: str | None = None
    vehicle_colour: str | None = None
    vehicle_type: str | None = None


class EvidenceComparisonRequest(BaseModel):
    first: CameraEvent
    second: CameraEvent


class EvidenceLink(BaseModel):
    first_event_id: str
    second_event_id: str
    score: float = Field(ge=0, le=100)
    relationship: Literal[
        "WEAK_CONNECTION",
        "POSSIBLE_SAME_APPEARANCE",
        "POSSIBLE_SAME_VEHICLE",
        "HIGH_PRIORITY_REVIEW",
    ]
    components: dict[str, float]
    reasons: list[str]
    journey_distance_km: float
    journey_plausible: bool
    human_review_required: bool = True


class AlertEvaluationRequest(BaseModel):
    event: CameraEvent
    hotspots: list[Hotspot]
    evidence_links: list[EvidenceLink] = Field(default_factory=list)


class Alert(BaseModel):
    alert_id: str
    event_id: str
    priority: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    status: Literal["NO_ALERT", "PENDING_REVIEW"]
    hotspot_id: str | None = None
    evidence_score: float
    reasons: list[str]
    human_review_required: bool = True
    evidence_policy: dict = Field(default_factory=dict)


class ReconstructRequest(BaseModel):
    claim: Claim
    events: list[CameraEvent]
    radius_km: float = Field(default=5.0, gt=0)
    minutes_before: int = Field(default=90, ge=0)
    minutes_after: int = Field(default=60, ge=0)


class TimelineItem(BaseModel):
    event_id: str
    camera_id: str | None = None
    timestamp: datetime
    distance_from_claim_km: float
    relevance_score: float = Field(ge=0, le=100)
    description: str
    media_url: str | None = None
    evidence_signals: list[str] = Field(default_factory=list)
    camera_trust_score: float | None = Field(default=None, ge=0, le=100)


class IncidentTimeline(BaseModel):
    claim_id: str
    start_time: datetime
    end_time: datetime
    items: list[TimelineItem]
    summary: str


class PatrolRequest(BaseModel):
    start: Location
    hotspots: list[Hotspot]
    baseline_order: list[str] = Field(default_factory=list)
    max_stops: int = Field(default=6, ge=1)
    fuel_l_per_100km: float = Field(default=10.0, gt=0)
    return_to_start: bool = True


class RouteMetrics(BaseModel):
    ordered_hotspot_ids: list[str]
    distance_km: float
    estimated_fuel_litres: float
    risk_covered: float
    coverage_percent: float
    protected_risk_per_km: float
    peak_risk_covered: float = 0.0
    peak_risk_coverage_percent: float = 0.0
    peak_risk_stops: int = 0


class PatrolComparison(BaseModel):
    baseline: RouteMetrics
    optimised: RouteMetrics
    distance_saved_km: float
    fuel_saved_litres: float
    protected_risk_per_km_improvement_percent: float
    coverage_change_points: float = 0.0
    peak_risk_coverage_change_points: float = 0.0
    peak_risk_retained: bool = False


class EnrichmentRow(BaseModel):
    hotspot_id: str
    saps_context_score: float = Field(default=50, ge=0, le=100)
    population_exposure_index: float = Field(default=50, ge=0, le=100)
    camera_coverage_score: float = Field(default=50, ge=0, le=100)
    response_success_score: float = Field(default=50, ge=0, le=100)


class EnrichmentRequest(BaseModel):
    hotspots: list[Hotspot]
    enrichments: list[EnrichmentRow]
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "claims": 0.55,
            "saps": 0.25,
            "camera_gap": 0.10,
            "response_gap": 0.10,
        }
    )
