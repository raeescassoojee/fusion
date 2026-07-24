# Shape enriched hotspots into route-optimizer inputs, grouped by metro.
# Routing runs within a metro only, never between metros.
# Reads:  data/curated/hotspots.json
# Writes: data/curated/route_inputs.json
#
# For the routing teammate (handbook p23). risk_weight = blended_risk_v2
# (claims + type-matched SAPS), the strongest available signal.

import json
import config

def build_route_inputs():
    data = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))
    hotspots = data if isinstance(data, list) else data["hotspots"]

    metros = {}
    for h in hotspots:
        stop = {
            "hotspot_id": h["hotspot_id"],
            "name": h["name"],
            "lat": h["latitude"],
            "lon": h["longitude"],
            "risk_weight": h.get("blended_risk_v2", h["risk_score"]),
            "claims_only_risk": h["risk_score"],
            "main_peril": h.get("main_peril"),
            "confidence_band": h.get("confidence", {}).get("band"),
            "peak_window": h.get("peak_window"),
        }
        metros.setdefault(h["metro"], []).append(stop)

    output = {}
    for metro, stops in metros.items():
        stops_sorted = sorted(stops, key=lambda s: s["risk_weight"], reverse=True)
        output[metro] = {
            "depot": stops_sorted[0]["hotspot_id"],   # start at highest-risk stop
            "stop_count": len(stops_sorted),
            "stops": stops_sorted,
        }

    payload = {
        "note": ("Route within a metro only. risk_weight = blended_risk_v2 "
                 "(claims + type-matched SAPS). peak_window gives the risk time "
                 "for time-aware routing. confidence_band flags data strength."),
        "weight_field": "risk_weight",
        "metros": output,
    }

    out = config.CURATED_DIR / "route_inputs.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for metro, block in output.items():
        print(f"{metro}: {block['stop_count']} stops, depot = {block['depot']}")
    print(f"\nSaved: {out.name}")

if __name__ == "__main__":
    build_route_inputs()