from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "operations" / "src")]

from sentinel_ops.aws_status import aws_status


if __name__ == "__main__":
    result = aws_status()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get("ready") else 2)
