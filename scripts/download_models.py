from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
from pathlib import Path


MODELS = {
    "yunet": (
        "face_detection_yunet_2023mar.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ),
    "sface": (
        "face_recognition_sface_2021dec.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ),
    "lpd_yunet": (
        "license_plate_detection_lpd_yunet_2023mar.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "license_plate_detection_yunet/"
        "license_plate_detection_lpd_yunet_2023mar.onnx",
    ),
}

LICENSES = {
    "yunet": (
        "face_detection_yunet_LICENSE.txt",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
        "face_detection_yunet/LICENSE",
    ),
    "sface": (
        "face_recognition_sface_LICENSE.txt",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
        "face_recognition_sface/LICENSE",
    ),
    "lpd_yunet": (
        "license_plate_detection_yunet_LICENSE.txt",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/"
        "license_plate_detection_yunet/LICENSE",
    ),
}


def download(name: str, destination: Path) -> Path:
    filename, url = MODELS[name]
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / filename
    if target.exists() and target.stat().st_size > 100_000:
        print(f"exists: {target}")
        return target
    with tempfile.NamedTemporaryFile(delete=False, dir=destination) as stream:
        temporary = Path(stream.name)
    try:
        print(f"downloading {name} from the official OpenCV Zoo")
        request = urllib.request.Request(url, headers={"User-Agent": "SentinelMesh/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size < 100_000:
            raise RuntimeError(
                f"downloaded file is unexpectedly small ({temporary.stat().st_size} bytes)"
            )
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        temporary.replace(target)
        print(f"saved: {target} sha256={digest}")
        return target
    finally:
        temporary.unlink(missing_ok=True)


def download_license(name: str, destination: Path) -> Path:
    filename, url = LICENSES[name]
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / filename
    if target.exists() and target.stat().st_size > 100:
        print(f"exists: {target}")
        return target
    print(f"downloading {name} model licence from the official OpenCV Zoo")
    request = urllib.request.Request(url, headers={"User-Agent": "SentinelMesh/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        content = response.read()
    if len(content) < 100:
        raise RuntimeError(f"downloaded licence is unexpectedly small ({len(content)} bytes)")
    target.write_bytes(content)
    print(f"saved: {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "models", nargs="*", choices=sorted(MODELS), default=None
    )
    parser.add_argument("--destination", default="models")
    args = parser.parse_args()
    for name in args.models or sorted(MODELS):
        download(name, Path(args.destination))
        download_license(name, Path(args.destination) / "licenses")


if __name__ == "__main__":
    main()
