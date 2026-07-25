from pathlib import Path

from sentinel_ops.roles_api import roles


def test_role_contract_matches_required_workspaces():
    payload = roles()
    by_id = {item["id"]: item for item in payload["roles"]}

    assert by_id["member"]["home"] == "property"
    assert "own cameras" in by_id["member"]["sees"]
    assert "individual claim files" in by_id["member"]["never"]

    assert by_id["fraud"]["home"] == "claims"
    assert "all claims" in by_id["fraud"]["sees"]
    assert "member camera control" in by_id["fraud"]["never"]

    assert by_id["security"]["home"] == "briefing"
    assert "patrol routes" in by_id["security"]["sees"]
    assert "claim amounts" in by_id["security"]["never"]

    assert "demo role selector" in payload["principle"]
    assert "authenticated identities" in payload["principle"]


def test_dashboard_has_strict_role_tab_sets_and_role_chooser():
    html_path = Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")

    assert 'id="roleGate"' in html
    assert 'data-role-choice="member"' in html
    assert 'data-role-choice="fraud"' in html
    assert 'data-role-choice="security"' in html

    assert "member:['property','live']" in html
    assert "fraud:['claims','evidence','movement','rewind','patterns']" in html
    assert "security:['briefing','patrol']" in html

    assert "showRoleGate();\n  loadAI();" in html
    assert "applyRole('fraud');" not in html

    assert 'id="mContribution"' in html
    assert 'id="mPoints"' in html
    assert "no live Vitality API is connected" in html
