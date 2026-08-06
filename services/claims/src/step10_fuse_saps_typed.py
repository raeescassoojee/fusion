# Step 10: Second SAPS fusion, crime-type-matched to each hotspot's main peril.
# Separate from step8 (generic theft). Both stats kept.
# Reads:  data/curated/hotspots.json, data/partner/saps_crime_by_type.xlsx
# Writes: data/curated/hotspots.json (adds saps_typed block + blended_risk_v2)
#         data/curated/saps_typed_summary.csv
#
# HONEST NOTE: SAPS categories matched to Discovery perils; latest year 2015-2016;
# precinct-level, so suburbs sharing a station share a score.

import json
import numpy as np
import pandas as pd
import config

def load_station_crime():
    path = config.PARTNER_DIR / "saps_2025_2026_by_station_crime.csv"
    df = pd.read_csv(path)
    df["Station"] = df["Station"].astype(str).str.upper().str.strip()
    df["Incidents"] = pd.to_numeric(df["Incidents"], errors="raise").clip(lower=0)
    wanted = {c for cats in config.PERIL_TO_SAPS_CATEGORIES.values() for c in cats}
    grouped = df[df["Crime_Category"].isin(wanted)].groupby(["Station", "Crime_Category"])["Incidents"].sum()
    data = {}
    for (station, crime), incidents in grouped.items():
        data.setdefault(station, {})[crime] = int(incidents)
    return data

def fuse_typed():
    hotspots = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))
    station_crime = load_station_crime()

    # matched raw count per hotspot, based on its main peril
    matched = {}
    for h in hotspots:
        key = h["name"].upper()
        station = config.SUBURB_TO_STATION.get(key)
        peril = h.get("main_peril", "")
        cats = config.PERIL_TO_SAPS_CATEGORIES.get(peril, [])
        breakdown = {c: station_crime.get(station, {}).get(c, 0) for c in cats}
        matched[h["name"]] = (station, peril, sum(breakdown.values()), breakdown)

    vals = [m[2] for m in matched.values()]
    lo, hi = min(vals), max(vals)
    med = float(np.median(vals))
    def norm(v): return round(100 * (v - lo) / (hi - lo), 1) if hi > lo else 0.0

    w = config.SAPS_TYPE_BLEND_WEIGHT
    summary = []
    for h in hotspots:
        station, peril, total, breakdown = matched[h["name"]]
        s_score = norm(total)
        blended_v2 = round((1 - w) * h["risk_score"] + w * s_score, 1)
        h["saps_typed"] = {
            "station": station.title() if station else None,
            "matched_peril": peril,
            "matched_categories": breakdown,
            "matched_total": total,
            "saps_typed_score": s_score,
            "corroborated": bool(total >= med),
            "year": "2025/2026",
        }
        h["blended_risk_v2"] = blended_v2
        summary.append({"suburb": h["name"], "station": station, "peril": peril,
                        "matched_total": total, "typed_score": s_score,
                        "claims_risk": h["risk_score"], "blended_v2": blended_v2,
                        "corroborated": bool(total >= med)})

    (config.CURATED_DIR / "hotspots.json").write_text(json.dumps(hotspots, indent=2), encoding="utf-8")
    import csv
    with open(config.CURATED_DIR / "saps_typed_summary.csv", "w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        wtr.writeheader()
        wtr.writerows(sorted(summary, key=lambda x: -x["blended_v2"]))

    print(f"Type-matched SAPS fused into {len(hotspots)} hotspots.")
    print(f"{'Suburb':22s} {'peril':14s} {'matched':>7s} {'typed':>6s} {'blend2':>6s} corrob")
    for r in sorted(summary, key=lambda x: -x["blended_v2"]):
        print(f"{r['suburb']:22s} {r['peril']:14s} {r['matched_total']:7d} "
              f"{r['typed_score']:6.1f} {r['blended_v2']:6.1f}  {'YES' if r['corroborated'] else 'no'}")

if __name__ == "__main__":
    fuse_typed()
