from datetime import datetime
from pathlib import Path

from sentinel_camera_ai.config import AppConfig
from sentinel_camera_ai.detectors.face import FaceSystem
from sentinel_camera_ai.detectors.plate import PlateSystem
from sentinel_camera_ai.matching import compare_events
from sentinel_camera_ai.pipeline import CameraAIPipeline, Candidate, CandidateBucket
from sentinel_camera_ai.detectors.plate import OCRResult
from sentinel_camera_ai.quality import TrustResult
from sentinel_camera_ai.synthetic import generate_demo_media

import cv2
import numpy as np
import pytest


def _test_config(tmp_path: Path) -> AppConfig:
    config = AppConfig.load("config/default.yaml")
    config.output_dir = str(tmp_path / "output")
    config.object_detection.backend = "heuristic"
    config.plate.backend = "contour"
    # Use automatic OCR discovery so tests unrelated to OCR still run when the
    # optional system-level Tesseract executable is not installed.
    config.plate.ocr_backend = "auto"
    config.face.backend = "haar"
    return config


def test_synthetic_plate_detects_and_reads(tmp_path):
    generate_demo_media(tmp_path / "media")
    frame = cv2.imread(str(tmp_path / "media" / "synthetic_reference.jpg"))
    system = PlateSystem(_test_config(tmp_path))
    if not system.ocr_engines:
        pytest.skip(
            "No OCR engine is installed. Install Tesseract 5 or PaddleOCR to run the plate-read assertion."
        )
    results = []
    for detection in system.detect(frame):
        results.append(system.read(detection.crop(frame, padding=0.04)))
    assert any(result.text == "AB12CDGP" for result in results)


def test_synthetic_face_detects_and_embeds(tmp_path):
    frame = cv2.imread("media/synthetic_face_fixture.png")
    assert frame is not None
    config = _test_config(tmp_path)
    config.face.backend = "yunet"
    face_system = FaceSystem(config)
    detections = face_system.detect(frame)
    assert len(detections) == 1
    assert detections[0].confidence >= 0.8
    embedding = face_system.embedding(frame, detections[0])
    assert embedding is not None
    assert embedding.size > 0


def test_end_to_end_two_camera_match(tmp_path):
    clips = generate_demo_media(tmp_path / "media")
    config = _test_config(tmp_path)
    pipeline = CameraAIPipeline(config)
    if not pipeline.plate_system.ocr_engines:
        pytest.skip(
            "No OCR engine is installed. Install Tesseract 5 or PaddleOCR to run the two-camera plate match."
        )
    first = pipeline.process_media(
        clips[0],
        camera_id="CAM01",
        mode="HEIGHTENED",
        start_timestamp=datetime.fromisoformat("2026-07-24T21:07:00+02:00"),
    )
    second = pipeline.process_media(
        clips[1],
        camera_id="CAM02",
        mode="HEIGHTENED",
        start_timestamp=datetime.fromisoformat("2026-07-24T21:07:04+02:00"),
    )
    first_vehicle = next(event for event, _ in first if event.plate.text == "AB12CDGP")
    second_vehicle = next(event for event, _ in second if event.plate.text == "AB12CDGP")
    comparison = compare_events(first_vehicle, second_vehicle)
    assert comparison.possible_same_vehicle.value is True
    assert comparison.possible_same_vehicle.score >= 0.9
    assert first_vehicle.camera_trust_score >= 60
    assert second_vehicle.camera_trust_score >= 60


def test_normal_mode_skips_plate_ocr(tmp_path):
    generate_demo_media(tmp_path / "media")
    config = _test_config(tmp_path)
    pipeline = CameraAIPipeline(config)
    results = pipeline.process_media(
        tmp_path / "media" / "synthetic_reference.jpg",
        camera_id="CAM01",
        mode="NORMAL",
        start_timestamp=datetime.fromisoformat("2026-07-24T21:07:00+02:00"),
    )
    assert results
    assert all(event.plate.text is None for event, _ in results)


def test_repeat_after_cooldown_starts_a_new_event_bucket(tmp_path):
    config = _test_config(tmp_path)
    config.video.dedupe_cooldown_seconds = 3.0
    pipeline = CameraAIPipeline(config)
    start = datetime.fromisoformat("2026-07-24T21:07:00+02:00")

    def candidate(at_seconds: int) -> Candidate:
        return Candidate(
            frame=np.zeros((80, 160, 3), dtype=np.uint8),
            frame_index=at_seconds,
            timestamp=start.replace(second=start.second + at_seconds),
            motion_score=0.2,
            faces=[],
            objects=[],
            plates=[],
            primary_vehicle=None,
            primary_person=None,
            plate_detection=None,
            plate_ocr=OCRResult("AB12CDGP", 0.9, "fixture"),
            vehicle_colour="Blue",
            vehicle_colour_confidence=0.8,
            upper_colour="Unknown",
            lower_colour="Unknown",
            appearance_confidence=0.0,
            direction="right",
            trust=TrustResult(
                score=80,
                metrics={
                    "sharpness": 80,
                    "lighting": 80,
                    "detection": 80,
                    "unobstructed": 80,
                    "resolution": 80,
                },
                raw={},
                reasons=[],
            ),
        )

    bucket = CandidateBucket(candidate(0))
    assert pipeline._matching_bucket([bucket], candidate(2)) is bucket
    assert pipeline._matching_bucket([bucket], candidate(5)) is None
