from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height


class Location(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class FaceEvidence(StrictModel):
    present: bool = False
    count: int = Field(default=0, ge=0)
    boxes: list[BoundingBox] = Field(default_factory=list)
    crop_paths: list[str] = Field(default_factory=list)
    embedding_ref: str | None = None
    detection_confidence: float | None = Field(default=None, ge=0, le=1)


class PlateEvidence(StrictModel):
    text: str | None = None
    display_text: str | None = None
    box: BoundingBox | None = None
    crop_url: str | None = None
    detection_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)


class VehicleEvidence(StrictModel):
    colour: str = "Unknown"
    type: str = "Unknown"
    make_model: str | None = None
    direction: str = "stationary_or_unknown"
    box: BoundingBox | None = None
    detection_confidence: float | None = Field(default=None, ge=0, le=1)


class AppearanceEvidence(StrictModel):
    upper_colour: str = "Unknown"
    lower_colour: str = "Unknown"
    cap: bool | None = None
    backpack: bool | None = None
    person_box: BoundingBox | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class QualityMetrics(StrictModel):
    sharpness: int = Field(ge=0, le=100)
    lighting: int = Field(ge=0, le=100)
    detection: int = Field(ge=0, le=100)
    unobstructed: int = Field(ge=0, le=100)
    resolution: int = Field(ge=0, le=100)


class CameraEvent(StrictModel):
    schema_version: str = "1.0"
    event_id: str
    camera_id: str
    timestamp: datetime
    mode: Literal["NORMAL", "HEIGHTENED"]
    location: Location
    source_media: str
    media_url: str
    frame_index: int = Field(default=0, ge=0)
    motion_score: float = Field(default=0, ge=0, le=1)
    face: FaceEvidence = Field(default_factory=FaceEvidence)
    plate: PlateEvidence = Field(default_factory=PlateEvidence)
    vehicle: VehicleEvidence = Field(default_factory=VehicleEvidence)
    appearance: AppearanceEvidence = Field(default_factory=AppearanceEvidence)
    camera_trust_score: int = Field(ge=0, le=100)
    quality_metrics: QualityMetrics
    trust_reasons: list[str] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value


class MatchDecision(StrictModel):
    value: bool
    score: float = Field(ge=0, le=1)
    evidence_strength: Literal["NONE", "LOW", "MEDIUM", "HIGH"]
    reasons: list[str] = Field(default_factory=list)


class ComparisonResult(StrictModel):
    event_a: str
    event_b: str
    possible_same_vehicle: MatchDecision
    possible_same_face: MatchDecision
    possible_same_appearance: MatchDecision
    warnings: list[str] = Field(default_factory=list)

