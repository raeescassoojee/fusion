from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ..config import AppConfig
from ..detection import Detection
from ..schemas import BoundingBox

LOGGER = logging.getLogger(__name__)

SUPPORTED_CLASSES = {"person", "car", "motorcycle", "bus", "truck"}


class ObjectDetector:
    """YOLO object detector with an explicitly labelled heuristic fallback."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.backend_name = "disabled"
        self.model = None
        self._initialise()

    def _initialise(self) -> None:
        backend = self.config.object_detection.backend
        if backend == "disabled":
            return
        if backend in {"auto", "yolo"}:
            try:
                from ultralytics import YOLO

                model_name = self.config.object_detection.yolo_model
                local_path = self.config.resolve(model_name)
                source = str(local_path) if local_path.exists() else model_name
                self.model = YOLO(source)
                self.backend_name = f"Ultralytics YOLO ({Path(model_name).name})"
                return
            except Exception as exc:
                LOGGER.warning("YOLO is unavailable; using heuristic fallback: %s", exc)
                if backend == "yolo":
                    raise RuntimeError(
                        "YOLO backend was requested but could not be loaded"
                    ) from exc
        if backend in {"auto", "heuristic"} and self.config.object_detection.allow_heuristic_fallback:
            self.backend_name = "OpenCV colour/contour heuristic fallback"
            return
        raise RuntimeError(f"object backend {backend!r} is unavailable")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self.model is not None:
            detections = self._detect_yolo(frame)
            if detections or not self.config.object_detection.allow_heuristic_fallback:
                return detections
            return self._detect_heuristic(frame)
        if self.backend_name.startswith("OpenCV"):
            return self._detect_heuristic(frame)
        return []

    def _detect_yolo(self, frame: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            source=frame,
            conf=self.config.object_detection.confidence,
            imgsz=self.config.object_detection.image_size,
            verbose=False,
        )
        detections: list[Detection] = []
        for result in results:
            names = result.names
            if result.boxes is None:
                continue
            xyxy = result.boxes.xyxy.detach().cpu().numpy()
            confidences = result.boxes.conf.detach().cpu().numpy()
            classes = result.boxes.cls.detach().cpu().numpy().astype(int)
            track_ids = (
                result.boxes.id.detach().cpu().numpy().astype(int)
                if result.boxes.id is not None
                else [None] * len(xyxy)
            )
            for coordinates, confidence, class_id, track_id in zip(
                xyxy, confidences, classes, track_ids
            ):
                label = str(names[class_id]).lower()
                if label not in SUPPORTED_CLASSES:
                    continue
                x1, y1, x2, y2 = [int(round(value)) for value in coordinates]
                if x2 <= x1 or y2 <= y1:
                    continue
                detections.append(
                    Detection(
                        kind=label,
                        box=BoundingBox(
                            x=max(0, x1),
                            y=max(0, y1),
                            width=max(1, x2 - x1),
                            height=max(1, y2 - y1),
                        ),
                        confidence=float(confidence),
                        class_id=int(class_id),
                        track_id=int(track_id) if track_id is not None else None,
                    )
                )
        return detections

    def _detect_heuristic(self, frame: np.ndarray) -> list[Detection]:
        """Fallback for controlled demo graphics, not a replacement for YOLO."""
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturation = hsv[:, :, 1]
        value = hsv[:, :, 2]
        mask = np.where((saturation > 42) & (value > 45), 255, 0).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = height * width
        detections: list[Detection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < frame_area * 0.008 or area > frame_area * 0.75:
                continue
            x, y, box_w, box_h = cv2.boundingRect(contour)
            if box_w < 25 or box_h < 25:
                continue
            aspect = box_w / box_h
            if aspect >= 1.15:
                kind = "car"
            elif aspect <= 0.82:
                kind = "person"
            else:
                continue
            area_ratio = area / frame_area
            confidence = min(0.68, max(0.30, 0.32 + area_ratio * 4))
            detections.append(
                Detection(
                    kind=kind,
                    box=BoundingBox(
                        x=int(x), y=int(y), width=int(box_w), height=int(box_h)
                    ),
                    confidence=round(float(confidence), 3),
                    attributes={"fallback": True},
                )
            )
        return sorted(
            detections,
            key=lambda item: item.box.width * item.box.height,
            reverse=True,
        )
