from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .schemas import CameraEvent


class EvidenceStore:
    def __init__(self, output_dir: str | Path):
        self.root = Path(output_dir).resolve()
        self.events_dir = self.root / "events"
        self.evidence_dir = self.root / "evidence"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def event_evidence_dir(self, event_id: str) -> Path:
        path = self.evidence_dir / event_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_image(self, event_id: str, name: str, image: np.ndarray) -> str:
        path = self.event_evidence_dir(event_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if image.size == 0:
            raise ValueError(f"cannot save empty image: {name}")
        if not cv2.imwrite(str(path), image):
            raise IOError(f"OpenCV could not write image: {path}")
        return path.relative_to(self.root).as_posix()

    def save_embedding(self, event_id: str, name: str, vector: np.ndarray) -> str:
        path = self.event_evidence_dir(event_id) / name
        np.save(path, np.asarray(vector, dtype=np.float32))
        return path.relative_to(self.root).as_posix()

    def save_event(self, event: CameraEvent) -> Path:
        path = self.events_dir / f"{event.event_id}.json"
        path.write_text(event.model_dump_json(indent=2), encoding="utf-8")
        return path

    def append_jsonl(self, event: CameraEvent) -> Path:
        path = self.root / "events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
        return path

