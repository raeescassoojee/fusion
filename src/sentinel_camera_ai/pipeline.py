from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from rapidfuzz.fuzz import ratio

from .annotate import draw_detections
from .colour import appearance_colours, dominant_colour
from .config import AppConfig, ModeConfig
from .detection import Detection, contains
from .detectors import FaceSystem, ObjectDetector, PlateSystem
from .detectors.plate import OCRResult, display_plate, normalize_plate
from .quality import TrustResult, calculate_trust
from .schemas import (
    AppearanceEvidence,
    BoundingBox,
    CameraEvent,
    FaceEvidence,
    Location,
    PlateEvidence,
    VehicleEvidence,
)
from .storage import EvidenceStore

LOGGER = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}
VEHICLE_KINDS = {"car", "truck", "bus", "motorcycle"}


@dataclass(slots=True)
class Candidate:
    frame: np.ndarray
    frame_index: int
    timestamp: datetime
    motion_score: float
    faces: list[Detection]
    objects: list[Detection]
    plates: list[Detection]
    primary_vehicle: Detection | None
    primary_person: Detection | None
    plate_detection: Detection | None
    plate_ocr: OCRResult
    vehicle_colour: str
    vehicle_colour_confidence: float
    upper_colour: str
    lower_colour: str
    appearance_confidence: float
    direction: str
    trust: TrustResult
    observations: int = 1
    model_notes: list[str] = field(default_factory=list)

    @property
    def plate_text(self) -> str:
        return normalize_plate(self.plate_ocr.text)

    @property
    def vehicle_type(self) -> str:
        return self.primary_vehicle.kind.title() if self.primary_vehicle else "Unknown"

    @property
    def quality_rank(self) -> float:
        plate_bonus = (
            self.plate_ocr.confidence * 20
            if self.plate_text
            else (self.plate_detection.confidence * 5 if self.plate_detection else 0)
        )
        return self.trust.score + plate_bonus


@dataclass(slots=True)
class CandidateBucket:
    best: Candidate
    observations: int = 1
    last_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.last_timestamp is None:
            self.last_timestamp = self.best.timestamp

    def add(self, candidate: Candidate) -> None:
        self.observations += 1
        self.last_timestamp = candidate.timestamp
        if candidate.quality_rank > self.best.quality_rank:
            candidate.observations = self.observations
            self.best = candidate
        else:
            self.best.observations = self.observations


class CameraAIPipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        self.face_system = FaceSystem(config)
        self.object_detector = ObjectDetector(config)
        self.plate_system = PlateSystem(config)
        self.store = EvidenceStore(config.resolve(config.output_dir))

    def process_media(
        self,
        input_path: str | Path,
        camera_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        mode: str = "HEIGHTENED",
        start_timestamp: datetime | None = None,
    ) -> list[tuple[CameraEvent, Path]]:
        path = Path(input_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        normalized_mode = mode.upper()
        if normalized_mode not in self.config.modes:
            raise ValueError(
                f"mode must be one of {sorted(self.config.modes)}, got {mode!r}"
            )
        mode_config = self.config.modes[normalized_mode]
        location = Location(
            latitude=self.config.camera.latitude if latitude is None else latitude,
            longitude=self.config.camera.longitude if longitude is None else longitude,
        )
        camera = camera_id or self.config.camera.id
        timestamp = start_timestamp or datetime.now(ZoneInfo(self.config.timezone))
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"OpenCV could not read image: {path}")
            candidate = self._analyse_frame(
                frame=frame,
                frame_index=0,
                timestamp=timestamp,
                motion_score=0.0,
                mode=normalized_mode,
                mode_config=mode_config,
                previous_vehicle_center=None,
            )
            if candidate is None:
                return []
            event, event_path = self._persist_candidate(
                candidate, path, camera, location, normalized_mode
            )
            return [(event, event_path)]
        if suffix in VIDEO_SUFFIXES:
            return self._process_video(
                path, camera, location, normalized_mode, mode_config, timestamp
            )
        raise ValueError(
            f"unsupported media extension {suffix!r}; supported images={sorted(IMAGE_SUFFIXES)}, videos={sorted(VIDEO_SUFFIXES)}"
        )

    def _process_video(
        self,
        path: Path,
        camera_id: str,
        location: Location,
        mode: str,
        mode_config: ModeConfig,
        start_timestamp: datetime,
    ) -> list[tuple[CameraEvent, Path]]:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise ValueError(f"OpenCV could not open video: {path}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = 25.0
        buckets: list[CandidateBucket] = []
        previous_gray: np.ndarray | None = None
        previous_vehicle_center: tuple[float, float] | None = None
        frame_index = -1
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_index += 1
                if frame_index % mode_config.frame_stride != 0:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                motion = self._motion_score(gray, previous_gray)
                previous_gray = gray
                if mode_config.motion_gate and motion < 0.015:
                    continue
                timestamp = start_timestamp + timedelta(seconds=frame_index / fps)
                frame_mode = mode_config
                if (
                    mode_config.run_plate
                    and frame_index % self.config.video.heavy_frame_interval != 0
                ):
                    frame_mode = mode_config.model_copy(update={"run_plate": False})
                candidate = self._analyse_frame(
                    frame=frame,
                    frame_index=frame_index,
                    timestamp=timestamp,
                    motion_score=motion,
                    mode=mode,
                    mode_config=frame_mode,
                    previous_vehicle_center=previous_vehicle_center,
                )
                if candidate is None:
                    continue
                if candidate.primary_vehicle is not None:
                    previous_vehicle_center = candidate.primary_vehicle.centre
                bucket = self._matching_bucket(buckets, candidate)
                if bucket is None:
                    buckets.append(CandidateBucket(best=candidate))
                else:
                    bucket.add(candidate)
        finally:
            capture.release()

        stable = [
            bucket
            for bucket in buckets
            if bucket.observations >= self.config.video.minimum_stable_observations
        ]
        if not stable and buckets:
            LOGGER.warning(
                "No candidate reached the stability threshold; keeping the best single observation"
            )
            stable = [max(buckets, key=lambda bucket: bucket.best.quality_rank)]
        stable.sort(key=lambda bucket: bucket.best.quality_rank, reverse=True)
        results: list[tuple[CameraEvent, Path]] = []
        for bucket in stable[: self.config.video.max_events_per_clip]:
            bucket.best.observations = bucket.observations
            results.append(
                self._persist_candidate(
                    bucket.best, path, camera_id, location, mode
                )
            )
        return results

    def _analyse_frame(
        self,
        frame: np.ndarray,
        frame_index: int,
        timestamp: datetime,
        motion_score: float,
        mode: str,
        mode_config: ModeConfig,
        previous_vehicle_center: tuple[float, float] | None,
    ) -> Candidate | None:
        objects = self.object_detector.detect(frame) if mode_config.run_vehicle else []
        faces = self.face_system.detect(frame) if mode_config.run_face else []
        plates = self.plate_system.detect(frame) if mode_config.run_plate else []
        vehicles = [item for item in objects if item.kind in VEHICLE_KINDS]
        persons = [item for item in objects if item.kind == "person"]
        primary_vehicle = self._largest(vehicles)
        primary_person = self._largest(persons)

        plate_detection, plate_ocr = self._best_plate(frame, plates)
        # A contour around an eye, logo or shirt detail can resemble a plate.
        # Without a supporting vehicle box, require readable OCR before keeping
        # the candidate. This still allows a close-up plate image when OCR works.
        if (
            plate_detection is not None
            and primary_vehicle is None
            and not plate_ocr.text
        ):
            plates = []
            plate_detection = None
            plate_ocr = OCRResult(None, 0.0, "none")
        if not (objects or faces or plate_detection):
            return None

        vehicle_colour, vehicle_colour_confidence = ("Unknown", 0.0)
        if primary_vehicle is not None:
            vehicle_colour, vehicle_colour_confidence = dominant_colour(
                primary_vehicle.crop(frame), centre_fraction=0.58
            )

        upper_colour, lower_colour, appearance_confidence = (
            "Unknown",
            "Unknown",
            0.0,
        )
        if mode_config.run_appearance and primary_person is not None:
            (
                upper_colour,
                lower_colour,
                appearance_confidence,
            ) = appearance_colours(frame, primary_person.box)

        direction = self._direction(
            previous_vehicle_center,
            primary_vehicle.centre if primary_vehicle is not None else None,
            frame.shape,
        )
        confidence_values = [item.confidence for item in objects + faces + plates]
        if plate_ocr.text:
            confidence_values.append(plate_ocr.confidence)
        evidence_box = (
            plate_detection.box
            if plate_detection is not None
            else primary_vehicle.box
            if primary_vehicle is not None
            else primary_person.box
            if primary_person is not None
            else faces[0].box
            if faces
            else None
        )
        trust = calculate_trust(
            frame,
            detection_confidences=confidence_values,
            evidence_box=evidence_box,
            config=self.config.trust,
        )
        notes: list[str] = []
        if "fallback" in (primary_vehicle.attributes if primary_vehicle else {}):
            notes.append("heuristic object detector used")
        if plate_detection and plate_detection.attributes.get("fallback"):
            notes.append("contour plate detector used")
        if self.face_system.detector_name.endswith("fallback"):
            notes.append("Haar face detector used")
        return Candidate(
            frame=frame.copy(),
            frame_index=frame_index,
            timestamp=timestamp,
            motion_score=motion_score,
            faces=faces,
            objects=objects,
            plates=plates,
            primary_vehicle=primary_vehicle,
            primary_person=primary_person,
            plate_detection=plate_detection,
            plate_ocr=plate_ocr,
            vehicle_colour=vehicle_colour,
            vehicle_colour_confidence=vehicle_colour_confidence,
            upper_colour=upper_colour,
            lower_colour=lower_colour,
            appearance_confidence=appearance_confidence,
            direction=direction,
            trust=trust,
            model_notes=notes,
        )

    def _best_plate(
        self, frame: np.ndarray, detections: list[Detection]
    ) -> tuple[Detection | None, OCRResult]:
        best_detection: Detection | None = None
        best_ocr = OCRResult(None, 0.0, "none")
        best_score = -1.0
        ranked = sorted(detections, key=lambda item: item.confidence, reverse=True)[:2]
        for detection in ranked:
            crop = detection.crop(frame, padding=0.04)
            result = self.plate_system.read(crop)
            combined = 0.58 * detection.confidence + 0.42 * result.confidence
            if combined > best_score:
                best_score = combined
                best_detection = detection
                best_ocr = result
        return best_detection, best_ocr

    @staticmethod
    def _largest(detections: list[Detection]) -> Detection | None:
        return (
            max(detections, key=lambda item: item.box.width * item.box.height)
            if detections
            else None
        )

    @staticmethod
    def _motion_score(current: np.ndarray, previous: np.ndarray | None) -> float:
        if previous is None or previous.shape != current.shape:
            return 1.0
        difference = cv2.absdiff(current, previous)
        changed = np.mean(difference > 18)
        return round(float(min(1.0, changed * 4.0)), 4)

    @staticmethod
    def _direction(
        previous: tuple[float, float] | None,
        current: tuple[float, float] | None,
        frame_shape: tuple[int, ...],
    ) -> str:
        if previous is None or current is None:
            return "stationary_or_unknown"
        dx, dy = current[0] - previous[0], current[1] - previous[1]
        threshold = max(frame_shape[0], frame_shape[1]) * 0.008
        if abs(dx) < threshold and abs(dy) < threshold:
            return "stationary_or_unknown"
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "toward_or_down" if dy > 0 else "away_or_up"

    @staticmethod
    def _candidate_compatible(a: Candidate, b: Candidate) -> bool:
        if a.plate_text and b.plate_text:
            # Within one short clip, one missing OCR character is common while
            # the same plate moves toward the edge. This threshold is used only
            # for de-duplicating observations from the same source clip.
            if ratio(a.plate_text, b.plate_text) >= 72:
                return True
        same_type = a.vehicle_type == b.vehicle_type and a.vehicle_type != "Unknown"
        same_colour = (
            a.vehicle_colour == b.vehicle_colour and a.vehicle_colour != "Unknown"
        )
        if same_type and same_colour:
            return True
        return (
            a.upper_colour == b.upper_colour
            and a.lower_colour == b.lower_colour
            and a.upper_colour != "Unknown"
        )

    def _matching_bucket(
        self, buckets: list[CandidateBucket], candidate: Candidate
    ) -> CandidateBucket | None:
        for bucket in buckets:
            if bucket.last_timestamp is None:
                continue
            gap_seconds = (candidate.timestamp - bucket.last_timestamp).total_seconds()
            if (
                0 <= gap_seconds <= self.config.video.dedupe_cooldown_seconds
                and self._candidate_compatible(bucket.best, candidate)
            ):
                return bucket
        return None

    def _persist_candidate(
        self,
        candidate: Candidate,
        source_path: Path,
        camera_id: str,
        location: Location,
        mode: str,
    ) -> tuple[CameraEvent, Path]:
        seed = (
            f"{source_path.resolve()}|{camera_id}|{candidate.timestamp.isoformat()}|"
            f"{candidate.frame_index}|{candidate.plate_text}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
        event_id = f"EVT-{digest}"
        best_frame_path = self.store.save_image(
            event_id, "best_frame.jpg", candidate.frame
        )

        all_detections = candidate.objects + candidate.faces
        if candidate.plate_detection is not None:
            all_detections.append(candidate.plate_detection)
        annotated = draw_detections(candidate.frame, all_detections)
        annotated_path = self.store.save_image(
            event_id, "annotated_frame.jpg", annotated
        )

        face_crop_paths: list[str] = []
        embedding_ref: str | None = None
        for index, detection in enumerate(candidate.faces):
            crop = detection.crop(candidate.frame, padding=0.08)
            if crop.size == 0:
                continue
            if self.config.face.save_crops:
                face_crop_paths.append(
                    self.store.save_image(
                        event_id, f"faces/face_{index}.jpg", crop
                    )
                )
            if index == 0:
                vector = self.face_system.embedding(candidate.frame, detection)
                if vector is not None:
                    embedding_ref = self.store.save_embedding(
                        event_id, "faces/face_0_embedding.npy", vector
                    )

        plate_crop_path: str | None = None
        if candidate.plate_detection is not None:
            crop = candidate.plate_detection.crop(candidate.frame, padding=0.04)
            if crop.size:
                plate_crop_path = self.store.save_image(
                    event_id, "plate.jpg", crop
                )

        vehicle = VehicleEvidence()
        if candidate.primary_vehicle is not None:
            vehicle = VehicleEvidence(
                colour=candidate.vehicle_colour,
                type=candidate.vehicle_type,
                make_model=None,
                direction=candidate.direction,
                box=candidate.primary_vehicle.box,
                detection_confidence=candidate.primary_vehicle.confidence,
            )

        appearance = AppearanceEvidence()
        if candidate.primary_person is not None:
            appearance = AppearanceEvidence(
                upper_colour=candidate.upper_colour,
                lower_colour=candidate.lower_colour,
                cap=None,
                backpack=None,
                person_box=candidate.primary_person.box,
                confidence=candidate.appearance_confidence,
            )

        plate = PlateEvidence()
        if candidate.plate_detection is not None:
            plate = PlateEvidence(
                text=candidate.plate_text or None,
                display_text=display_plate(candidate.plate_text),
                box=candidate.plate_detection.box,
                crop_url=plate_crop_path,
                detection_confidence=candidate.plate_detection.confidence,
                ocr_confidence=(
                    candidate.plate_ocr.confidence
                    if candidate.plate_ocr.text
                    else None
                ),
            )

        face_confidence = (
            max(item.confidence for item in candidate.faces)
            if candidate.faces
            else None
        )
        event = CameraEvent(
            schema_version=self.config.schema_version,
            event_id=event_id,
            camera_id=camera_id,
            timestamp=candidate.timestamp,
            mode=mode,
            location=location,
            source_media=str(source_path),
            media_url=best_frame_path,
            frame_index=candidate.frame_index,
            motion_score=candidate.motion_score,
            face=FaceEvidence(
                present=bool(candidate.faces),
                count=len(candidate.faces),
                boxes=[item.box for item in candidate.faces],
                crop_paths=face_crop_paths,
                embedding_ref=embedding_ref,
                detection_confidence=face_confidence,
            ),
            plate=plate,
            vehicle=vehicle,
            appearance=appearance,
            camera_trust_score=candidate.trust.score,
            quality_metrics=candidate.trust.metrics,
            trust_reasons=candidate.trust.reasons,
            model_versions={
                "objects": self.object_detector.backend_name,
                "face": self.face_system.detector_name,
                "face_embedding": (
                    "OpenCV SFace" if self.face_system.embedding_enabled else "disabled"
                ),
                "plate_detection": self.plate_system.detector.backend_name,
                "plate_ocr": self.plate_system.ocr_name,
            },
            metadata={
                "annotated_media_url": annotated_path,
                "observation_count": candidate.observations,
                "plate_raw_text": candidate.plate_ocr.raw_text,
                "ocr_backend": candidate.plate_ocr.backend,
                "vehicle_colour_confidence": candidate.vehicle_colour_confidence,
                "quality_raw": candidate.trust.raw,
                "model_notes": candidate.model_notes,
                "possible_plate_in_vehicle": (
                    contains(candidate.primary_vehicle.box, candidate.plate_detection.box)
                    if candidate.primary_vehicle is not None
                    and candidate.plate_detection is not None
                    else None
                ),
            },
        )
        event_path = self.store.save_event(event)
        self.store.append_jsonl(event)
        return event, event_path
