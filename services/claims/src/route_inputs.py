# Shape hotspots into route-optimizer inputs, grouped by metro.
# Routing runs within a metro, never between metros.
# Reads:  data/curated/hotspots.json
# Writes: data/curated/route_inputs.json
#
# For the routing teammate (handbook p23): risk-weighted stops per metro.

import json
import config

def build_route_inputs():
    data = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))
    hotspots = data["hotspots"]

    metros = {}
    for h in hotspots:
        metro = h["metro"]
        metros.setdefault(metro, []).append({
            "id": h["area_id"],
            "suburb": h["suburb"],
            "lat": h["location"]["lat"],
            "lon": h["location"]["lon"],
            "risk_weight": h["risk_score"],   # optimizer maximizes coverage of this
        })

    # Depot = highest-risk stop in each metro (a sensible patrol start point).
    # The routing teammate can override this with a real depot later.
    output_metros = {}
    for metro, stops in metros.items():
        stops_sorted = sorted(stops, key=lambda s: s["risk_weight"], reverse=True)
        output_metros[metro] = {
            "depot": stops_sorted[0]["id"],   # start at the worst hotspot
            "stops": stops_sorted,
        }

    payload = {
        "note": "Route within a metro only. risk_weight is the 0-100 Risk Pulse score.",
        "metros": output_metros,
    }

    out = config.CURATED_DIR / "route_inputs.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for metro, block in output_metros.items():
        print(f"{metro}: {len(block['stops'])} stops, depot = {block['depot']}")
    print(f"\nSaved: {out.name}")

if __name__ == "__main__":
    build_route_inputs()