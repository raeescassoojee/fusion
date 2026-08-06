from datetime import datetime

import pytest
from pydantic import ValidationError

from sentinel_camera_ai.config import AppConfig
from sentinel_camera_ai.schemas import CameraEvent, Location, QualityMetrics


def test_default_config_loads():
    config = AppConfig.load("config/default.yaml")
    assert config.modes["NORMAL"].run_plate is False
    assert config.modes["HEIGHTENED"].run_plate is True
    assert abs(sum(config.trust.normalized_weights().values()) - 1.0) < 1e-9


def test_event_requires_timezone():
    common = dict(
        event_id="EVT-TEST",
        camera_id="CAM01",
        mode="NORMAL",
        location=Location(latitude=-25.7, longitude=28.3),
        source_media="fixture.jpg",
        media_url="evidence/fixture.jpg",
        camera_trust_score=70,
        quality_metrics=QualityMetrics(
            sharpness=70,
            lighting=70,
            detection=70,
            unobstructed=70,
            resolution=70,
        ),
    )
    with pytest.raises(ValidationError):
        CameraEvent(timestamp=datetime(2026, 7, 24, 21, 7), **common)


def test_event_serialization_round_trip():
    event = CameraEvent(
        event_id="EVT-TEST",
        camera_id="CAM01",
        timestamp=datetime.fromisoformat("2026-07-24T21:07:00+02:00"),
        mode="HEIGHTENED",
        location=Location(latitude=-25.7, longitude=28.3),
        source_media="fixture.jpg",
        media_url="evidence/fixture.jpg",
        camera_trust_score=70,
        quality_metrics=QualityMetrics(
            sharpness=70,
            lighting=70,
            detection=70,
            unobstructed=70,
            resolution=70,
        ),
    )
    loaded = CameraEvent.model_validate_json(event.model_dump_json())
    assert loaded == event

