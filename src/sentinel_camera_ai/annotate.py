from __future__ import annotations

import cv2
import numpy as np

from .detection import Detection


COLOURS = {
    "face": (0, 190, 255),
    "plate": (0, 255, 0),
    "person": (255, 100, 0),
    "car": (255, 0, 180),
    "truck": (255, 0, 180),
    "bus": (255, 0, 180),
    "motorcycle": (255, 0, 180),
}


def draw_detections(frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
    output = frame.copy()
    for detection in detections:
        colour = COLOURS.get(detection.kind, (230, 230, 230))
        box = detection.box
        cv2.rectangle(output, (box.x, box.y), (box.x2, box.y2), colour, 2)
        label = f"{detection.kind} {detection.confidence:.2f}"
        (text_w, text_h), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1
        )
        top = max(0, box.y - text_h - 8)
        cv2.rectangle(
            output,
            (box.x, top),
            (box.x + text_w + 6, top + text_h + 8),
            colour,
            -1,
        )
        cv2.putText(
            output,
            label,
            (box.x + 3, top + text_h + 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (15, 25, 35),
            1,
            cv2.LINE_AA,
        )
    return output

