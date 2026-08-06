from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .schemas import BoundingBox


@dataclass(slots=True)
class Detection:
    kind: str
    box: BoundingBox
    confidence: float
    class_id: int | None = None
    track_id: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def crop(self, frame: np.ndarray, padding: float = 0.0) -> np.ndarray:
        height, width = frame.shape[:2]
        pad_x = int(self.box.width * padding)
        pad_y = int(self.box.height * padding)
        x1 = max(0, self.box.x - pad_x)
        y1 = max(0, self.box.y - pad_y)
        x2 = min(width, self.box.x2 + pad_x)
        y2 = min(height, self.box.y2 + pad_y)
        return frame[y1:y2, x1:x2].copy()

    @property
    def centre(self) -> tuple[float, float]:
        return (
            self.box.x + self.box.width / 2,
            self.box.y + self.box.height / 2,
        )


def intersection_over_union(a: BoundingBox, b: BoundingBox) -> float:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    intersection = (x2 - x1) * (y2 - y1)
    union = a.width * a.height + b.width * b.height - intersection
    return intersection / union if union else 0.0


def contains(outer: BoundingBox, inner: BoundingBox, margin: float = 0.15) -> bool:
    pad_x = outer.width * margin
    pad_y = outer.height * margin
    return (
        inner.x >= outer.x - pad_x
        and inner.y >= outer.y - pad_y
        and inner.x2 <= outer.x2 + pad_x
        and inner.y2 <= outer.y2 + pad_y
    )

