# Step 8: Fuse SAPS station theft data into hotspots.
# Adds corroboration flag + blended risk score (claims + SAPS).
# Reads:  data/curated/hotspots.json, data/partner/saps_theft_by_station.csv
# Writes: data/curated/hotspots.json (enriched, in place)
#         data/curated/saps_summary.csv (per-suburb SAPS lookup, for the team)
#
# HONEST NOTE: police precincts cover several suburbs, so some suburbs share
# a station and therefore a SAPS score. SAPS file is theft-only, years 2005-2017.

import json
import numpy as np
import pandas as pd
import config

def fuse():
    hotspots = json.loads((config.CURATED_DIR / "hotspots.json").read_text(encoding="utf-8"))

    saps = pd.read_csv(config.PARTNER_DIR / "saps_theft_by_station.csv")
    saps["STATION"] = saps["Police Station"].str.upper().str.strip()
    latest_year = sorted(saps["Year"].unique())[-1]
    recent = saps[saps["Year"] == latest_year]
    station_theft = recent.groupby("STATION")["Incidents"].sum()

    # SAPS theft for each pilot suburb via its station
    mapped = {}
    for sub, station in config.SUBURB_TO_STATION.items():
        mapped[sub] = int(station_theft.get(station, 0))

    lo, hi = min(mapped.values()), max(mapped.values())
    def norm(v):
        return round(100 * (v - lo) / (hi - lo), 1) if hi > lo else 0.0
    median_theft = float(np.median(list(mapped.values())))

    w = config.SAPS_BLEND_WEIGHT
    summary_rows = []
    for h in hotspots:
        key = h["name"].upper()
        station = config.SUBURB_TO_STATION.get(key)
        theft = mapped.get(key, 0)
        s_score = norm(theft)
        blended = round((1 - w) * h["risk_score"] + w * s_score, 1)

        h["saps"] = {
            "station": station.title() if station else None,
            "theft_incidents": theft,
            "theft_year": latest_year,
            "saps_score": s_score,
            "corroborated": bool(theft >= median_theft),
        }
        h["blended_risk_score"] = blended

        summary_rows.append({
            "suburb": h["name"], "station": station,
            "saps_theft": theft, "saps_score": s_score,
            "claims_risk": h["risk_score"], "blended_risk": blended,
            "corroborated": bool(theft >= median_theft),
        })

    (config.CURATED_DIR / "hotspots.json").write_text(
        json.dumps(hotspots, indent=2), encoding="utf-8")
    pd.DataFrame(summary_rows).sort_values("blended_risk", ascending=False)\
        .to_csv(config.CURATED_DIR / "saps_summary.csv", index=False)

    print(f"Fused SAPS theft (year {latest_year}) into {len(hotspots)} hotspots.")
    print(f"{'Suburb':22s} {'claims':>7s} {'sapsN':>6s} {'blend':>6s}  corrob")
    for r in sorted(summary_rows, key=lambda x: x["blended_risk"], reverse=True):
        print(f"{r['suburb']:22s} {r['claims_risk']:7.1f} {r['saps_score']:6.1f} "
              f"{r['blended_risk']:6.1f}  {'YES' if r['corroborated'] else 'no'}")

if __name__ == "__main__":
    fuse()