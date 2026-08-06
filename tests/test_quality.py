import cv2
import numpy as np

from sentinel_camera_ai.config import AppConfig
from sentinel_camera_ai.quality import calculate_trust
from sentinel_camera_ai.schemas import BoundingBox


def test_clear_image_scores_higher_than_blurred_image():
    image = np.full((720, 1280, 3), 150, dtype=np.uint8)
    for x in range(50, 1200, 80):
        cv2.line(image, (x, 80), (x, 650), (15, 15, 15), 3)
    blurred = cv2.GaussianBlur(image, (41, 41), 0)
    config = AppConfig.load("config/default.yaml")
    box = BoundingBox(x=250, y=180, width=500, height=280)
    clear_result = calculate_trust(image, [0.9], box, config.trust)
    blurred_result = calculate_trust(blurred, [0.9], box, config.trust)
    assert clear_result.metrics.sharpness > blurred_result.metrics.sharpness
    assert clear_result.score > blurred_result.score


def test_clipped_box_reduces_unobstructed_metric():
    image = np.full((480, 640, 3), 130, dtype=np.uint8)
    config = AppConfig.load("config/default.yaml")
    clipped = calculate_trust(
        image,
        [0.8],
        BoundingBox(x=0, y=0, width=200, height=100),
        config.trust,
    )
    centred = calculate_trust(
        image,
        [0.8],
        BoundingBox(x=150, y=100, width=200, height=100),
        config.trust,
    )
    assert centred.metrics.unobstructed > clipped.metrics.unobstructed

