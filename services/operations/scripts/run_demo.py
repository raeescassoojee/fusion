import json
from pathlib import Path

from sentinel_ops.demo import run_demo

result = run_demo()
Path("outputs").mkdir(exist_ok=True)
Path("outputs/demo_output.json").write_text(
    json.dumps(result, indent=2),
    encoding="utf-8",
)
print("Evidence score:", result["evidence"]["score"])
print("Alert priority:", result["alert"]["priority"])
print("Timeline events:", len(result["timeline"]["items"]))
print("Baseline distance:", result["patrol"]["baseline"]["distance_km"], "km")
print("Optimised distance:", result["patrol"]["optimised"]["distance_km"], "km")
print("Fuel saved:", result["patrol"]["fuel_saved_litres"], "L")
print(
    "Protected risk/km improvement:",
    result["patrol"]["protected_risk_per_km_improvement_percent"],
    "%",
)
