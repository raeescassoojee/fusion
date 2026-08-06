from __future__ import annotations

import cv2
import numpy as np

from .schemas import BoundingBox


COLOUR_HUES = {
    "Red": 0,
    "Orange": 15,
    "Yellow": 30,
    "Green": 60,
    "Blue": 110,
    "Purple": 145,
}


def _circular_hue_distance(a: float, b: float) -> float:
    difference = abs(a - b)
    return min(difference, 180 - difference)


def classify_hsv(hue: float, saturation: float, value: float) -> str:
    if value < 45:
        return "Black"
    if saturation < 30:
        if value > 205:
            return "White"
        if value > 105:
            return "Grey"
        return "Black"
    return min(COLOUR_HUES, key=lambda name: _circular_hue_distance(hue, COLOUR_HUES[name]))


def dominant_colour(image: np.ndarray, centre_fraction: float = 0.58) -> tuple[str, float]:
    if image.size == 0:
        return "Unknown", 0.0
    height, width = image.shape[:2]
    fraction = min(max(centre_fraction, 0.2), 1.0)
    crop_w = max(1, int(width * fraction))
    crop_h = max(1, int(height * fraction))
    x1 = (width - crop_w) // 2
    y1 = (height - crop_h) // 2
    roi = image[y1 : y1 + crop_h, x1 : x1 + crop_w]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    pixels = hsv.reshape(-1, 3)
    if len(pixels) == 0:
        return "Unknown", 0.0

    saturation = pixels[:, 1]
    value = pixels[:, 2]
    useful = pixels[(value > 25) & (value < 245)]
    if len(useful) < max(20, len(pixels) * 0.05):
        useful = pixels
    median_h, median_s, median_v = np.median(useful, axis=0)
    colour = classify_hsv(float(median_h), float(median_s), float(median_v))

    spread = float(np.std(useful[:, 0])) + float(np.std(useful[:, 2])) / 2
    confidence = max(0.2, min(0.95, 1.0 - spread / 120.0))
    return colour, round(confidence, 3)


def crop_box(frame: np.ndarray, box: BoundingBox) -> np.ndarray:
    height, width = frame.shape[:2]
    x1, y1 = max(0, box.x), max(0, box.y)
    x2, y2 = min(width, box.x2), min(height, box.y2)
    return frame[y1:y2, x1:x2].copy()


def appearance_colours(frame: np.ndarray, box: BoundingBox) -> tuple[str, str, float]:
    person = crop_box(frame, box)
    if person.size == 0:
        return "Unknown", "Unknown", 0.0
    height = person.shape[0]
    upper = person[int(height * 0.18) : int(height * 0.55)]
    lower = person[int(height * 0.55) : int(height * 0.92)]
    upper_colour, upper_confidence = dominant_colour(upper, centre_fraction=0.72)
    lower_colour, lower_confidence = dominant_colour(lower, centre_fraction=0.72)
    return upper_colour, lower_colour, round((upper_confidence + lower_confidence) / 2, 3)

