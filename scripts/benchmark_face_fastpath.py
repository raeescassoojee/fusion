"""Repeatable local benchmark for the finals biometric hot path.

Run from the repository root after installing the operations service:
    python scripts/benchmark_face_fastpath.py --runs 20

The script uses a temporary database and the bundled consented/synthetic face fixture.
It never modifies the judging database.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT / "services" / "operations" / "src")]

from sentinel_ops.main import app


def summary(values: list[float]) -> dict[str, float | int]:
    data = np.asarray(values, dtype=np.float64)
    return {
        "runs": int(data.size),
        "p50_ms": round(float(np.percentile(data, 50)), 1),
        "p95_ms": round(float(np.percentile(data, 95)), 1),
        "mean_ms": round(float(statistics.fmean(values)), 1),
        "min_ms": round(float(data.min()), 1),
        "max_ms": round(float(data.max()), 1),
    }


def timed_post(client: TestClient, path: str, **kwargs):
    started = time.perf_counter()
    response = client.post(path, **kwargs)
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned {response.status_code}: {response.text[:500]}")
    return elapsed, response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=REPO_ROOT / "media" / "synthetic_face_fixture.png",
    )
    args = parser.parse_args()
    image = args.fixture.read_bytes()

    with tempfile.TemporaryDirectory(prefix="sentinel-fastpath-") as temp_dir:
        os.environ["SENTINEL_DATABASE_PATH"] = str(Path(temp_dir) / "benchmark.db")
        detection: list[float] = []
        one_face: list[float] = []
        three_face: list[float] = []
        with TestClient(app) as client:
            # Warm model files and OpenCV kernels before recording latency.
            timed_post(
                client,
                "/api/member/face-detect",
                files={"image": ("face.png", image, "image/png")},
            )
            client.delete("/api/demo/reset?full=true")

            for _ in range(max(1, args.runs)):
                elapsed, _ = timed_post(
                    client,
                    "/api/member/face-detect",
                    files={"image": ("face.png", image, "image/png")},
                )
                detection.append(elapsed)

                elapsed, _ = timed_post(
                    client,
                    "/api/member/face-sightings/batch",
                    files=[
                        ("images", (f"track-1-{index}.png", image, "image/png"))
                        for index in range(3)
                    ],
                    data={
                        "user_id": "USR-001",
                        "camera_id": "CAM-U1-01",
                        "candidates_json": json.dumps([
                            {
                                "track_id": "TRACK-1",
                                "sample_index": index,
                                "confidence": 0.99,
                                "quality": 90 - index,
                            }
                            for index in range(3)
                        ]),
                    },
                )
                one_face.append(elapsed)

                candidates = [
                    {
                        "track_id": f"TRACK-{track}",
                        "sample_index": sample,
                        "confidence": 0.99,
                        "quality": 90 - sample,
                    }
                    for track in range(1, 4)
                    for sample in range(3)
                ]
                elapsed, _ = timed_post(
                    client,
                    "/api/member/face-sightings/batch",
                    files=[
                        (
                            "images",
                            (f"track-{track}-{sample}.png", image, "image/png"),
                        )
                        for track in range(1, 4)
                        for sample in range(3)
                    ],
                    data={
                        "user_id": "USR-001",
                        "camera_id": "CAM-U1-01",
                        "candidates_json": json.dumps(candidates),
                    },
                )
                three_face.append(elapsed)

            server_ledger = client.get("/api/performance").json()

    report = {
        "fixture": str(args.fixture),
        "conditions": "warm local TestClient; temporary SQLite database",
        "client_round_trip": {
            "detection": summary(detection),
            "one_face_batch": summary(one_face),
            "three_face_batch": summary(three_face),
        },
        "server_ledger": server_ledger,
        "targets_ms": {
            "one_face_p50": 700,
            "one_face_p95": 1200,
            "three_face_p95": 2000,
        },
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
