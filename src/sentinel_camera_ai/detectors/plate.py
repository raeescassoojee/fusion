from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import AppConfig
from ..detection import Detection, intersection_over_union
from ..schemas import BoundingBox
from .lpd_yunet import LPDYuNet

LOGGER = logging.getLogger(__name__)

PLATE_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def normalize_plate(text: str | None) -> str:
    if not text:
        return ""
    return "".join(character for character in text.upper() if character.isalnum())


def display_plate(text: str | None) -> str | None:
    normalized = normalize_plate(text)
    if not normalized:
        return None
    if normalized.endswith(("GP", "GZ", "MP", "NW", "FS", "NC", "EC", "WC", "LP")):
        return f"{normalized[:-2]} {normalized[-2:]}"
    return normalized


def _nms(detections: list[Detection], threshold: float = 0.55) -> list[Detection]:
    output: list[Detection] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        if all(intersection_over_union(detection.box, kept.box) < threshold for kept in output):
            output.append(detection)
    return output


class PlateDetector:
    def __init__(self, config: AppConfig):
        self.config = config
        self.model = None
        self.lpd_model: LPDYuNet | None = None
        self.backend_name = "disabled"
        self._initialise()

    def _initialise(self) -> None:
        backend = self.config.plate.backend
        if backend == "disabled":
            return
        model_path = self.config.resolve(self.config.plate.yolo_model)
        lpd_path = self.config.resolve(self.config.plate.lpd_yunet_model)
        if backend in {"auto", "yolo"} and model_path.exists():
            try:
                from ultralytics import YOLO

                self.model = YOLO(str(model_path))
                self.backend_name = f"YOLO plate detector ({model_path.name})"
                return
            except Exception as exc:
                LOGGER.warning("YOLO plate detector could not be loaded: %s", exc)
                if backend == "yolo":
                    raise
        if backend in {"auto", "lpd_yunet"} and lpd_path.exists():
            try:
                self.lpd_model = LPDYuNet(
                    str(lpd_path),
                    confidence_threshold=max(
                        0.45, self.config.plate.min_detection_confidence
                    ),
                )
                self.backend_name = f"OpenCV LPD-YuNet ({lpd_path.name}) + contour"
                return
            except Exception as exc:
                LOGGER.warning("LPD-YuNet could not be loaded: %s", exc)
                if backend == "lpd_yunet":
                    raise
        if backend in {"auto", "contour"}:
            self.backend_name = "OpenCV contour plate fallback"
            return
        if backend == "yolo":
            raise RuntimeError(f"plate model was not found: {model_path}")
        raise RuntimeError(f"plate backend {backend!r} is unavailable")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if self.model is not None:
            primary = self._detect_yolo(frame)
            return primary or self._detect_contours(frame)
        if self.lpd_model is not None:
            primary = self._detect_lpd(frame)
            return _nms(primary + self._detect_contours(frame))[:8]
        if self.backend_name.startswith("OpenCV"):
            return self._detect_contours(frame)
        return []

    def _detect_lpd(self, frame: np.ndarray) -> list[Detection]:
        output: list[Detection] = []
        try:
            results = self.lpd_model.infer(frame)
        except Exception as exc:
            LOGGER.warning("LPD-YuNet inference failed: %s", exc)
            return output
        height, width = frame.shape[:2]
        for row in results:
            points = np.asarray(row[:8], dtype=float).reshape(4, 2)
            x1 = max(0, int(np.floor(points[:, 0].min())))
            y1 = max(0, int(np.floor(points[:, 1].min())))
            x2 = min(width, int(np.ceil(points[:, 0].max())))
            y2 = min(height, int(np.ceil(points[:, 1].max())))
            if x2 <= x1 or y2 <= y1:
                continue
            box_width, box_height = x2 - x1, y2 - y1
            aspect = box_width / max(box_height, 1)
            area_ratio = (box_width * box_height) / max(width * height, 1)
            if not 1.8 <= aspect <= 7.5 or not 0.0002 <= area_ratio <= 0.18:
                continue
            output.append(
                Detection(
                    kind="plate",
                    box=BoundingBox(
                        x=x1, y=y1, width=box_width, height=box_height
                    ),
                    confidence=float(row[-1]),
                    attributes={
                        "corners": points.tolist(),
                        "source": "lpd_yunet",
                    },
                )
            )
        return output

    def _detect_yolo(self, frame: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            source=frame,
            conf=self.config.plate.min_detection_confidence,
            imgsz=960,
            verbose=False,
        )
        detections: list[Detection] = []
        for result in results:
            if result.boxes is None:
                continue
            for coordinates, confidence in zip(
                result.boxes.xyxy.detach().cpu().numpy(),
                result.boxes.conf.detach().cpu().numpy(),
            ):
                x1, y1, x2, y2 = [int(round(value)) for value in coordinates]
                if x2 > x1 and y2 > y1:
                    detections.append(
                        Detection(
                            kind="plate",
                            box=BoundingBox(
                                x=max(0, x1),
                                y=max(0, y1),
                                width=max(1, x2 - x1),
                                height=max(1, y2 - y1),
                            ),
                            confidence=float(confidence),
                        )
                    )
        return _nms(detections)

    def _detect_contours(self, frame: np.ndarray) -> list[Detection]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        frame_area = height * width
        candidates: list[Detection] = []

        bright = cv2.inRange(gray, 145, 255)
        bright = cv2.morphologyEx(
            bright,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)),
            iterations=2,
        )
        contours, _ = cv2.findContours(bright, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates.extend(self._score_contours(frame, contours, frame_area))

        blackhat = cv2.morphologyEx(
            gray,
            cv2.MORPH_BLACKHAT,
            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 7)),
        )
        gradient = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1)
        gradient = np.absolute(gradient)
        minimum, maximum = float(gradient.min()), float(gradient.max())
        if maximum > minimum:
            gradient = ((gradient - minimum) / (maximum - minimum) * 255).astype(np.uint8)
            gradient = cv2.morphologyEx(
                gradient,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (17, 3)),
            )
            thresholded = cv2.threshold(
                gradient, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
            )[1]
            contours, _ = cv2.findContours(
                thresholded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            candidates.extend(self._score_contours(frame, contours, frame_area))
        return _nms(candidates)[:8]

    def _score_contours(
        self,
        frame: np.ndarray,
        contours: list[np.ndarray],
        frame_area: int,
    ) -> list[Detection]:
        output: list[Detection] = []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < frame_area * 0.00035 or area > frame_area * 0.12:
                continue
            x, y, box_w, box_h = cv2.boundingRect(contour)
            if box_w < 45 or box_h < 12:
                continue
            aspect = box_w / max(box_h, 1)
            if not 2.0 <= aspect <= 7.0:
                continue
            crop = gray[y : y + box_h, x : x + box_w]
            if crop.size == 0:
                continue
            mean_brightness = float(np.mean(crop))
            edges = cv2.Canny(crop, 70, 180)
            edge_density = float(np.mean(edges > 0))
            aspect_score = max(0.0, 1.0 - abs(aspect - 4.2) / 4.2)
            brightness_score = max(0.0, 1.0 - abs(mean_brightness - 205) / 205)
            text_score = min(1.0, edge_density / 0.22)
            confidence = 0.25 + 0.32 * aspect_score + 0.23 * brightness_score + 0.20 * text_score
            if confidence < self.config.plate.min_detection_confidence:
                continue
            output.append(
                Detection(
                    kind="plate",
                    box=BoundingBox(
                        x=int(x), y=int(y), width=int(box_w), height=int(box_h)
                    ),
                    confidence=round(min(0.82, confidence), 3),
                    attributes={"fallback": True},
                )
            )
        return output


@dataclass(slots=True)
class OCRResult:
    text: str | None
    confidence: float
    backend: str
    raw_text: str | None = None


class TesseractPlateOCR:
    def __init__(self, config: AppConfig):
        self.config = config
        configured = os.getenv("TESSERACT_CMD", "").strip()
        candidates = [configured, shutil.which("tesseract")]
        if os.name == "nt":
            candidates.extend(
                [
                    str(Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Tesseract-OCR" / "tesseract.exe"),
                    str(Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe"),
                    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
                ]
            )
        self.executable = next(
            (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
            None,
        )
        self.available = self.executable is not None
        self.name = "Tesseract 5"

    def read(self, crop: np.ndarray) -> OCRResult:
        if not self.available or crop.size == 0:
            return OCRResult(None, 0.0, self.name)
        import pytesseract
        from pytesseract import Output

        if self.executable:
            pytesseract.pytesseract.tesseract_cmd = str(self.executable)

        variants = _ocr_variants(crop)
        best = OCRResult(None, 0.0, self.name)
        observed_texts: list[str] = []
        tesseract_config = (
            "--oem 3 --psm 7 "
            f"-c tessedit_char_whitelist={PLATE_CHARACTERS}"
        )
        for variant in variants:
            try:
                data = pytesseract.image_to_data(
                    variant,
                    config=tesseract_config,
                    output_type=Output.DICT,
                )
            except Exception as exc:
                LOGGER.warning("Tesseract OCR failed: %s", exc)
                continue
            tokens: list[str] = []
            confidences: list[float] = []
            for token, confidence in zip(data.get("text", []), data.get("conf", [])):
                normalized = normalize_plate(token)
                try:
                    numeric_confidence = float(confidence)
                except (TypeError, ValueError):
                    numeric_confidence = -1
                if normalized and numeric_confidence >= 0:
                    tokens.append(normalized)
                    confidences.append(numeric_confidence / 100)
            text = normalize_plate("".join(tokens))
            if not (
                self.config.plate.minimum_length
                <= len(text)
                <= self.config.plate.maximum_length
            ):
                continue
            observed_texts.append(text)
            confidence = sum(confidences) / len(confidences) if confidences else 0.0
            # Tesseract can return a correct whitelist-constrained string with a
            # zero word confidence. In that case, repeated agreement across
            # preprocessing variants is used as a conservative fallback.
            if confidence <= 0:
                agreement = observed_texts.count(text)
                confidence = min(0.72, 0.42 + 0.08 * agreement)
            if confidence > best.confidence:
                best = OCRResult(
                    text=text,
                    confidence=round(min(1.0, confidence), 3),
                    backend=self.name,
                    raw_text=" ".join(tokens),
                )
        return best


class PaddlePlateOCR:
    def __init__(self, config: AppConfig):
        self.config = config
        self.engine = None
        self.name = "PaddleOCR"
        try:
            from paddleocr import PaddleOCR

            try:
                self.engine = PaddleOCR(
                    lang="en",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except TypeError:
                self.engine = PaddleOCR(use_angle_cls=False, lang="en")
        except Exception as exc:
            LOGGER.info("PaddleOCR is unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self.engine is not None

    def read(self, crop: np.ndarray) -> OCRResult:
        if self.engine is None or crop.size == 0:
            return OCRResult(None, 0.0, self.name)
        best = OCRResult(None, 0.0, self.name)
        try:
            pairs: list[tuple[str, float]] = []
            if hasattr(self.engine, "predict"):
                predictions = self.engine.predict(crop)
                for prediction in predictions or []:
                    payload = getattr(prediction, "json", prediction)
                    if callable(payload):
                        payload = payload()
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            payload = None
                    pairs.extend(_extract_paddle_pairs(payload))
            if hasattr(self.engine, "ocr"):
                result = self.engine.ocr(crop, cls=False)
                lines = result[0] if result and isinstance(result[0], list) else result
                for line in lines or []:
                    if not isinstance(line, (list, tuple)) or len(line) < 2:
                        continue
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        pairs.append((str(text_info[0]), float(text_info[1])))
            for raw, confidence in pairs:
                normalized = normalize_plate(raw)
                if (
                    self.config.plate.minimum_length
                    <= len(normalized)
                    <= self.config.plate.maximum_length
                    and confidence > best.confidence
                ):
                    best = OCRResult(
                        text=normalized,
                        confidence=round(confidence, 3),
                        backend=self.name,
                        raw_text=raw,
                    )
        except Exception as exc:
            LOGGER.warning("PaddleOCR inference failed: %s", exc)
        return best


def _extract_paddle_pairs(payload: object) -> list[tuple[str, float]]:
    """Read PaddleOCR 3.x JSON without depending on one minor-version shape."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        for text_key, score_key in (
            ("rec_texts", "rec_scores"),
            ("texts", "scores"),
        ):
            texts = payload.get(text_key)
            scores = payload.get(score_key)
            if isinstance(texts, list) and isinstance(scores, list):
                return [
                    (str(text), float(score))
                    for text, score in zip(texts, scores)
                    if text is not None and score is not None
                ]
        for text_key, score_key in (
            ("rec_text", "rec_score"),
            ("text", "score"),
        ):
            text = payload.get(text_key)
            score = payload.get(score_key)
            if text is not None and score is not None:
                try:
                    return [(str(text), float(score))]
                except (TypeError, ValueError):
                    pass
        pairs: list[tuple[str, float]] = []
        for value in payload.values():
            pairs.extend(_extract_paddle_pairs(value))
        return pairs
    if isinstance(payload, (list, tuple)):
        if (
            len(payload) >= 2
            and isinstance(payload[0], str)
            and isinstance(payload[1], (int, float))
        ):
            return [(payload[0], float(payload[1]))]
        pairs: list[tuple[str, float]] = []
        for value in payload:
            pairs.extend(_extract_paddle_pairs(value))
        return pairs
    for attribute in ("res", "data"):
        value = getattr(payload, attribute, None)
        if value is not None:
            return _extract_paddle_pairs(value)
    return []


def _ocr_variants(crop: np.ndarray) -> list[np.ndarray]:
    scale = max(2.0, 220 / max(crop.shape[1], 1))
    resized = cv2.resize(
        crop,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 40, 40)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(
        clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    return [resized, otsu, adaptive]


class PlateSystem:
    def __init__(self, config: AppConfig):
        self.config = config
        self.detector = PlateDetector(config)
        self.ocr_engines: list[object] = []
        backend = config.plate.ocr_backend
        if backend in {"auto", "paddle"}:
            paddle = PaddlePlateOCR(config)
            if paddle.available:
                self.ocr_engines.append(paddle)
            elif backend == "paddle":
                raise RuntimeError("PaddleOCR was requested but is unavailable")
        if backend in {"auto", "tesseract"}:
            tesseract = TesseractPlateOCR(config)
            if tesseract.available:
                self.ocr_engines.append(tesseract)
            elif backend == "tesseract":
                raise RuntimeError("Tesseract was requested but is unavailable")

    @property
    def ocr_name(self) -> str:
        return " + ".join(engine.name for engine in self.ocr_engines) or "disabled"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return self.detector.detect(frame)

    def read(self, crop: np.ndarray) -> OCRResult:
        best = OCRResult(None, 0.0, "none")
        for engine in self.ocr_engines:
            result = engine.read(crop)
            if result.confidence > best.confidence:
                best = result
        if best.confidence < self.config.plate.min_ocr_confidence:
            return OCRResult(None, best.confidence, best.backend, best.raw_text)
        return best
