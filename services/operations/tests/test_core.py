from sentinel_ops.demo import run_demo

def test_complete_demo_loop():
    result = run_demo()
    assert result["evidence"]["score"] >= 70
    assert result["alert"]["status"] == "PENDING_REVIEW"
    assert len(result["timeline"]["items"]) >= 2
    assert result["patrol"]["optimised"]["protected_risk_per_km"] > 0
