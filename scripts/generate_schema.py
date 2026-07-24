from __future__ import annotations

import json
from pathlib import Path

from sentinel_camera_ai.schemas import CameraEvent, ComparisonResult


def main() -> None:
    destination = Path("schemas")
    destination.mkdir(parents=True, exist_ok=True)
    files = {
        "event-v1.schema.json": CameraEvent.model_json_schema(),
        "comparison-v1.schema.json": ComparisonResult.model_json_schema(),
    }
    for name, schema in files.items():
        path = destination / name
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()

