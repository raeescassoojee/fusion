from sentinel_ops.claims_bridge import build_metro_patrol, list_metros
from sentinel_ops.integrated_demo import run_integrated_demo


def test_cassoojee_snapshot_loads_and_routes():
    metros = list_metros()
    assert "Gauteng" in metros
    assert "Cape Town" in metros

    comparison, hotspots, _, metadata = build_metro_patrol("Gauteng")
    assert hotspots
    assert all(hotspot.metro == "Gauteng" for hotspot in hotspots)
    assert comparison.optimised.distance_km > 0
    assert metadata["source"] in {"cassoojee-live", "cassoojee-snapshot"}


def test_integrated_demo_has_graphics_payload():
    output = run_integrated_demo("Gauteng")
    assert output["evidence"]["score"] > 0
    assert output["timeline"]["items"]
    assert output["patrol"]["optimised"]["distance_km"] > 0
