# Step 7: Publish hotspots as clean JSON for the API/frontend to consume.
# Reads:  data/curated/step6_hotspots.csv
# Writes: data/curated/hotspots.json   (the contract other services read)
#
# Shape matches handbook page 27: GET /hotspots -> scores, components, geofence.

import json
import pandas as pd
import config

# Simple circular geofence radius per hotspot (metres). Handbook: small polygon/circle.
GEOFENCE_RADIUS_M = 1500

def publish():
    df = pd.read_csv(config.CURATED_DIR / "step6_hotspots.csv")

    hotspots = []
    for _, r in df.iterrows():
        hotspots.append({
            "area_id": r["SUBURB_CLEAN"].lower().replace(" ", "-"),
            "suburb": r["SUBURB_CLEAN"],
            "metro": r["METRO"],
            "location": {"lat": r["lat"], "lon": r["lon"]},
            "geofence": {"type": "circle", "radius_m": GEOFENCE_RADIUS_M},
            "risk_score": r["risk_score"],
            "components": {
                "frequency": r["norm_frequency"],
                "severity": r["norm_severity"],
                "recency": r["norm_recency"],
                "peak_time": r["norm_peak"],
            },
            "stats": {
                "incident_count": int(r["incident_count"]),
                "total_cost": round(float(r["total_cost"]), 2),
            },
            "formula_version": r["formula_version"],
        })

    payload = {
        "generated_from": "Discovery claims workbook (pilot)",
        "pilot_metros": sorted(df["METRO"].unique().tolist()),
        "hotspot_count": len(hotspots),
        "hotspots": hotspots,
    }

    out = config.CURATED_DIR / "hotspots.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Published {len(hotspots)} hotspots -> {out.name}")
    print("This JSON is the contract your teammates' API and map read from.")

if __name__ == "__main__":
    publish()
    