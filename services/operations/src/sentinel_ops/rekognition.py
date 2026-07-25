"""Amazon Rekognition biometric recognition on stored evidence crops.

The brief requires biometric recognition. Locally this runs YuNet; this module
adds the managed AWS path the handbook recommends (event crop -> S3/bytes ->
Rekognition Image), so the same evidence can be corroborated by a managed
service and shown as such in the UI.

Design rules:
- Never raises. If boto3, credentials or the region are unavailable the caller
  gets {"available": False, "reason": ...} and the local result still stands.
- DetectFaces only. This reports face PRESENCE and quality, not identity.
  No face collection, no IndexFaces, no SearchFacesByImage - so nothing here
  builds a biometric watchlist of ordinary members.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

AWS_REGION = os.getenv("AWS_REGION", "af-south-1")
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Rekognition Image byte-payload limit


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "service": "amazon-rekognition",
        "reason": reason,
    }


def detect_faces(image_bytes: bytes) -> dict[str, Any]:
    """Run Rekognition DetectFaces over raw image bytes."""
    if not image_bytes:
        return _unavailable("empty image")

    if len(image_bytes) > MAX_IMAGE_BYTES:
        return _unavailable(
            f"image is {len(image_bytes) // 1024} KB; Rekognition byte limit is 5 MB"
        )

    try:
        import boto3
    except ImportError:
        return _unavailable("boto3 is not installed")

    try:
        client = boto3.client("rekognition", region_name=AWS_REGION)
        response = client.detect_faces(
            Image={"Bytes": image_bytes},
            Attributes=["DEFAULT"],
        )
    except Exception as exc:  # noqa: BLE001 - local result must survive
        return _unavailable(f"{type(exc).__name__}: {exc}")

    details = response.get("FaceDetails", [])
    faces = []
    for face in details:
        quality = face.get("Quality", {})
        faces.append(
            {
                "confidence": round(float(face.get("Confidence", 0.0)), 2),
                "brightness": round(float(quality.get("Brightness", 0.0)), 1),
                "sharpness": round(float(quality.get("Sharpness", 0.0)), 1),
                "bounding_box": face.get("BoundingBox"),
            }
        )

    return {
        "available": True,
        "service": "amazon-rekognition",
        "operation": "DetectFaces",
        "region": AWS_REGION,
        "face_count": len(faces),
        "faces": faces,
        "top_confidence": max((f["confidence"] for f in faces), default=0.0),
        "request_id": response.get("ResponseMetadata", {}).get("RequestId"),
        "note": "Face presence and quality only. Not an identity match.",
    }


def detect_faces_in_file(path: str | Path) -> dict[str, Any]:
    """Read an evidence crop from disk and run DetectFaces over it."""
    file_path = Path(path)
    if not file_path.is_file():
        return _unavailable(f"no evidence image at {file_path.name}")
    try:
        return detect_faces(file_path.read_bytes())
    except OSError as exc:
        return _unavailable(f"could not read image: {exc}")
