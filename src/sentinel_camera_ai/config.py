from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraConfig(Model):
    id: str = "CAM01"
    latitude: float = -25.797
    longitude: float = 28.301


class FaceConfig(Model):
    backend: Literal["auto", "yunet", "haar", "disabled"] = "auto"
    yunet_model: str = "models/face_detection_yunet_2023mar.onnx"
    sface_model: str = "models/face_recognition_sface_2021dec.onnx"
    min_confidence: float = Field(default=0.72, ge=0, le=1)
    save_crops: bool = True


class ObjectConfig(Model):
    backend: Literal["auto", "yolo", "heuristic", "disabled"] = "auto"
    yolo_model: str = "yolo11n.pt"
    confidence: float = Field(default=0.40, ge=0, le=1)
    image_size: int = Field(default=640, ge=320, le=1920)
    allow_heuristic_fallback: bool = True


class PlateConfig(Model):
    backend: Literal["auto", "yolo", "lpd_yunet", "contour", "disabled"] = "auto"
    yolo_model: str = "models/license_plate_detector.pt"
    lpd_yunet_model: str = (
        "models/license_plate_detection_lpd_yunet_2023mar.onnx"
    )
    min_detection_confidence: float = Field(default=0.35, ge=0, le=1)
    ocr_backend: Literal["auto", "paddle", "tesseract", "disabled"] = "auto"
    min_ocr_confidence: float = Field(default=0.30, ge=0, le=1)
    minimum_length: int = Field(default=4, ge=1)
    maximum_length: int = Field(default=10, ge=4)


class VideoConfig(Model):
    max_events_per_clip: int = Field(default=4, ge=1, le=100)
    dedupe_cooldown_seconds: float = Field(default=3.0, ge=0)
    minimum_stable_observations: int = Field(default=2, ge=1, le=30)
    heavy_frame_interval: int = Field(default=10, ge=1, le=300)


class TrustConfig(Model):
    sharpness_weight: float = 0.25
    lighting_weight: float = 0.20
    detection_weight: float = 0.30
    unobstructed_weight: float = 0.15
    resolution_weight: float = 0.10

    def normalized_weights(self) -> dict[str, float]:
        values = {
            "sharpness": self.sharpness_weight,
            "lighting": self.lighting_weight,
            "detection": self.detection_weight,
            "unobstructed": self.unobstructed_weight,
            "resolution": self.resolution_weight,
        }
        total = sum(values.values())
        if total <= 0:
            raise ValueError("trust weights must sum to a positive number")
        return {key: value / total for key, value in values.items()}


class ModeConfig(Model):
    frame_stride: int = Field(default=4, ge=1, le=120)
    motion_gate: bool = False
    run_face: bool = True
    run_plate: bool = True
    run_vehicle: bool = True
    run_appearance: bool = True


class AwsConfig(Model):
    enabled: bool = False
    profile: str | None = "sentinel-dev"
    region: str = "af-south-1"
    bucket: str = ""
    evidence_prefix: str = "evidence"
    events_prefix: str = "events"
    ingestion_url: str = ""
    api_token_env: str = "SENTINEL_API_TOKEN"


def default_modes() -> dict[str, ModeConfig]:
    return {
        "NORMAL": ModeConfig(
            frame_stride=8,
            run_face=False,
            run_plate=False,
            run_vehicle=True,
            run_appearance=False,
        ),
        "HEIGHTENED": ModeConfig(frame_stride=2),
    }


class AppConfig(Model):
    schema_version: str = "1.0"
    output_dir: str = "output"
    timezone: str = "Africa/Johannesburg"
    camera: CameraConfig = Field(default_factory=CameraConfig)
    face: FaceConfig = Field(default_factory=FaceConfig)
    object_detection: ObjectConfig = Field(default_factory=ObjectConfig)
    plate: PlateConfig = Field(default_factory=PlateConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    trust: TrustConfig = Field(default_factory=TrustConfig)
    modes: dict[str, ModeConfig] = Field(default_factory=default_modes)
    aws: AwsConfig = Field(default_factory=AwsConfig)
    project_root: Path = Field(default_factory=lambda: Path("."))

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).resolve()
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        raw["project_root"] = config_path.parent.parent
        config = cls.model_validate(raw)
        required = {"NORMAL", "HEIGHTENED"}
        missing = required - set(config.modes)
        if missing:
            raise ValueError(f"missing required modes: {sorted(missing)}")
        return config

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.project_root / candidate
