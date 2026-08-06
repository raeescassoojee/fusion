from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import cv2

from .config import AppConfig


def run_doctor(config: AppConfig) -> dict[str, object]:
    yunet = config.resolve(config.face.yunet_model)
    sface = config.resolve(config.face.sface_model)
    plate = config.resolve(config.plate.yolo_model)
    lpd_plate = config.resolve(config.plate.lpd_yunet_model)
    checks = {
        "python": {
            "ok": sys.version_info >= (3, 11),
            "value": sys.version.split()[0],
        },
        "opencv": {"ok": True, "value": cv2.__version__},
        "ffmpeg": {"ok": shutil.which("ffmpeg") is not None, "value": shutil.which("ffmpeg")},
        "tesseract": {
            "ok": shutil.which("tesseract") is not None,
            "value": shutil.which("tesseract"),
        },
        "ultralytics": {
            "ok": importlib.util.find_spec("ultralytics") is not None,
            "value": "optional - recommended for real person/vehicle detection",
        },
        "paddleocr": {
            "ok": importlib.util.find_spec("paddleocr") is not None,
            "value": "optional - Tesseract fallback is supported",
        },
        "yunet_model": {
            "ok": yunet.exists(),
            "value": str(yunet),
        },
        "sface_model": {
            "ok": sface.exists(),
            "value": str(sface),
        },
        "plate_yolo_model": {
            "ok": plate.exists(),
            "value": str(plate),
        },
        "lpd_yunet_model": {
            "ok": lpd_plate.exists(),
            "value": str(lpd_plate),
        },
    }
    required_ok = all(
        checks[name]["ok"]
        for name in ("python", "opencv", "ffmpeg", "tesseract")
    )
    return {
        "required_ok": required_ok,
        "checks": checks,
        "notes": [
            "The core controlled demo can run with the OpenCV/Tesseract fallbacks.",
            "Install Ultralytics and provide plate weights before evaluating real CCTV footage.",
            "Download YuNet and SFace for better face detection and anonymous face comparison.",
        ],
    }
