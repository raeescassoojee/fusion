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

    assert by_id["security"]["home"] == "dispatch"
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
    # Abed's finals frontend deliberately consolidates fraud review into one
    # claim-centred workspace; evidence, movement, rewind and patterns are tools
    # inside that selected case instead of separate top-level navigation tabs.
    assert "fraud:['claims']" in html
    # Security follows the same finals pattern: one incident response desk.
    assert "security:['dispatch']" in html

    assert "showRoleGate();\n  loadAI();" in html
    assert "applyRole('fraud');" not in html

    assert 'id="mContribution"' in html
    assert 'id="mPoints"' in html
    assert "no live Vitality API is connected" in html


def test_finals_workspace_order_and_map_layers_are_visible():
    html_path = Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")

    # Regional risk stays available, but the incomplete patrol comparison,
    # technical demo consoles and WhatsApp log are intentionally removed.
    planning = html.index('class="panel security-planning-panel"')
    assert planning > html.index('id="securityMap"')
    assert 'id="securityPatrolComparison"' not in html
    assert 'id="secConsoleGrid"' not in html
    assert 'id="secNotifications"' not in html
    assert 'id="secWhatsAppDemo"' not in html
    assert "function secSelectedRiskSource" in html
    assert "secRenderRiskMap();secRenderHotspots();" in html
    assert "setTimeout(()=>{if(secSimRunning)secTick();},250)" in html
    assert "duration=2200" in html

    # Mapbox Standard needs custom operational layers in its top slot; otherwise
    # the three per-house markers can exist in GeoJSON but render below the map.
    assert "function memberMapAddOperationalLayer" in html
    assert "if(memberMapProvider==='mapbox')layer.slot='top'" in html
    assert "memberMapAddOperationalLayer(map,{id:'mesh-camera-points'" in html
    assert "memberMapAddOperationalLayer(map,{id:'mesh-camera-rings'" in html
    assert "memberMapAddOperationalLayer(map,{id:'mesh-trail-line'" in html
    assert "memberMapAddOperationalLayer(map,{id:'mesh-trail-arrows'" in html
    assert "'text-keep-upright':false" in html
    assert "display_name:'Thandi',household:'10 Ness Avenue'" in html
    assert "display_name:'Amy',household:'11 Ness Avenue'" in html
    assert "display_name:'Fatima',household:'12 Ness Avenue'" in html
    assert "function memberStrictIntruderTrail" in html
    assert ".filter(point=>allowedIds.has(String(point.sighting_id||'')))" in html
    assert "trailChanged" in html
    assert "memberMeshRequestSeq" in html
    assert "loadMemberMesh();},1200)" in html
    assert "routeCoordinates.push(exact)" in html
    assert "Killarney Avenue live heat map" not in html
    assert "Killarney Avenue coordinates" not in html


def test_claims_demo_uses_supplied_rows_and_prepared_real_clips():
    html_path = Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")

    assert 'id="openEvidenceDemoClaim"' in html
    assert "showPreparedCameraInbox(d)" in html
    assert "Real workbook claims + claim-matched consented video" in html
    assert "source_claim_ids:['INC-002']" in html
    assert "Police case · ${cEsc(c.police_case_number)}" in html
    assert "AI populated information" in html
    assert "Ready for review" in html
    assert "${Math.round(ai.completion)}% complete" in html
    assert "${pct}% complete" in html
    assert "source_claim_ids:['INC-003']" in html
