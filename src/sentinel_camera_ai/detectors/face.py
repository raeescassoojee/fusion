from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from ..config import AppConfig
from ..detection import Detection
from ..schemas import BoundingBox

LOGGER = logging.getLogger(__name__)


class FaceSystem:
    """YuNet face detection with a built-in Haar fallback and optional SFace."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.detector_name = "disabled"
        self.detector = None
        self.recognizer = None
        self._initialise_detector()
        self._initialise_recognizer()

    def _initialise_detector(self) -> None:
        backend = self.config.face.backend
        if backend == "disabled":
            return
        yunet_path = self.config.resolve(self.config.face.yunet_model)
        if backend in {"auto", "yunet"} and yunet_path.exists():
            try:
                self.detector = cv2.FaceDetectorYN_create(
                    str(yunet_path),
                    "",
                    (320, 320),
                    self.config.face.min_confidence,
                    0.3,
                    5000,
                )
                self.detector_name = f"OpenCV YuNet ({yunet_path.name})"
                return
            except Exception as exc:
                LOGGER.warning("YuNet could not be loaded: %s", exc)
                if backend == "yunet":
                    raise

        if backend in {"auto", "haar"}:
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(str(cascade_path))
            if cascade.empty():
                raise RuntimeError(f"OpenCV Haar cascade could not be loaded: {cascade_path}")
            self.detector = cascade
            self.detector_name = "OpenCV Haar fallback"
            return
        raise RuntimeError(f"face backend {backend!r} is unavailable")

    def _initialise_recognizer(self) -> None:
        model_path = self.config.resolve(self.config.face.sface_model)
        if not model_path.exists():
            return
        try:
            self.recognizer = cv2.FaceRecognizerSF_create(str(model_path), "")
        except Exception as exc:
            LOGGER.warning("SFace could not be loaded: %s", exc)

    @property
    def embedding_enabled(self) -> bool:
        return self.recognizer is not None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self.detector is None:
            return []
        height, width = frame.shape[:2]
        detections: list[Detection] = []
        if self.detector_name.startswith("OpenCV YuNet"):
            self.detector.setInputSize((width, height))
            _, faces = self.detector.detect(frame)
            if faces is None:
                return []
            for face in faces:
                x, y, box_w, box_h = [int(round(value)) for value in face[:4]]
                x, y = max(0, x), max(0, y)
                box_w = min(width - x, max(1, box_w))
                box_h = min(height - y, max(1, box_h))
                landmarks = face[4:14].astype(float).reshape(5, 2).tolist()
                confidence = float(face[-1])
                detections.append(
                    Detection(
                        kind="face",
                        box=BoundingBox(x=x, y=y, width=box_w, height=box_h),
                        confidence=max(0.0, min(1.0, confidence)),
                        attributes={
                            "landmarks": landmarks,
                            "yunet_row": face.astype(np.float32),
                        },
                    )
                )
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = self.detector.detectMultiScale(
                gray,
                scaleFactor=1.12,
                minNeighbors=5,
                minSize=(30, 30),
            )
            for x, y, box_w, box_h in faces:
                detections.append(
                    Detection(
                        kind="face",
                        box=BoundingBox(
                            x=int(x), y=int(y), width=int(box_w), height=int(box_h)
                        ),
                        confidence=0.65,
                    )
                )
        return sorted(detections, key=lambda item: item.confidence, reverse=True)

    def embedding(self, frame: np.ndarray, detection: Detection) -> np.ndarray | None:
        if self.recognizer is None:
            return None
        try:
            yunet_row = detection.attributes.get("yunet_row")
            if yunet_row is not None:
                aligned = self.recognizer.alignCrop(frame, yunet_row)
            else:
                crop = detection.crop(frame, padding=0.08)
                if crop.size == 0:
                    return None
                aligned = cv2.resize(crop, (112, 112))
            feature = self.recognizer.feature(aligned)
            vector = np.asarray(feature, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            return vector / norm if norm > 1e-12 else None
        except Exception as exc:
            LOGGER.warning("SFace embedding failed: %s", exc)
            return None

