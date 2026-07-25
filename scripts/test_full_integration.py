from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ["SENTINEL_DATABASE_PATH"] = str(
    Path(tempfile.gettempdir()) / "sentinel_full_integration_test.db"
)

from sentinel_ops.main import app  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    db_path = Path(os.environ["SENTINEL_DATABASE_PATH"])
    if db_path.exists():
        db_path.unlink()

    client = TestClient(app)
    event_paths = [
        ROOT / "demo-output/events/EVT-4ADFB703C2.json",
        ROOT / "demo-output/events/EVT-314D9D0C2C.json",
    ]

    print("1. Checking claims bridge...")
    hotspots = client.get("/api/claims/hotspots", params={"metro": "Gauteng"})
    hotspots.raise_for_status()
    hotspot_payload = hotspots.json()
    print(
        f"   Loaded {len(hotspot_payload['hotspots'])} Gauteng hotspots "
        f"from {hotspot_payload['data_source']['source']}"
    )

    print("2. Posting camera events through the camera-ai adapter...")
    event_results = []
    for path in event_paths:
        response = client.post("/api/events/camera-ai", json=load_json(path))
        response.raise_for_status()
        result = response.json()
        event_results.append(result)
        print(
            f"   {result['event']['event_id']}: "
            f"{len(result['candidate_links'])} candidate links, "
            f"alert={result['alert']['priority']}"
        )

    print("3. Posting a matching claim and reconstructing the incident...")
    claim = {
        "claim_id": "CLM-INTEGRATION-001",
        "incident_time": "2026-07-24T21:10:00+02:00",
        "location": {"latitude": -25.797, "longitude": 28.301},
        "claim_type": "Vehicle Theft",
        "claim_amount": 420000,
        "plate_text": "AB12CDGP",
        "vehicle_colour": "Blue",
        "vehicle_type": "Car",
    }
    claim_response = client.post("/api/claims", json=claim)
    claim_response.raise_for_status()
    claim_result = claim_response.json()
    print(f"   Timeline contains {len(claim_result['timeline']['items'])} events")

    print("4. Running patrol optimisation using live merged claims outputs...")
    route = client.get("/api/routes/metro/Gauteng")
    route.raise_for_status()
    route_result = route.json()
    print(
        f"   Distance: {route_result['baseline']['distance_km']} km -> "
        f"{route_result['optimised']['distance_km']} km"
    )
    print(
        f"   Fuel: {route_result['baseline']['estimated_fuel_litres']} L -> "
        f"{route_result['optimised']['estimated_fuel_litres']} L"
    )
    print(
        f"   Protected risk/km improvement: "
        f"{route_result['protected_risk_per_km_improvement_percent']}%"
    )

    print("5. Checking stored operational state...")
    status = client.get("/api/storage/status")
    status.raise_for_status()
    print("  ", status.json())

    output = {
        "hotspots": hotspot_payload,
        "event_results": event_results,
        "claim_result": claim_result,
        "route_result": route_result,
        "storage": status.json(),
    }
    target = ROOT / "integration-output/full_integration_result.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nPASS: Full backend integration succeeded.")
    print(f"Output written to {target}")


if __name__ == "__main__":
    main()
