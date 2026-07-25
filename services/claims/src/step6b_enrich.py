# Step 6b: Enrich hotspots into the required delivery schema.
# Adds: hotspot_id, main_peril, peak_window (day/start/end) + confidence.
# Reads:  data/curated/step4_pilot.csv, data/curated/step5_geocoded.csv
# Writes: data/curated/hotspots.json  (the required Person-1 delivery schema)

import json
import numpy as np
import pandas as pd
import config

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday"]
PEAK_HOURS = range(18, 24)  # evening band for peak-time score

def normalize(s):
    lo, hi = s.min(), s.max()
    return (s - lo) / (hi - lo) if hi > lo else s * 0

def incident_type(item_type):
    # Brief: identify home invasion vs vehicle theft.
    return {"Vehicle": "Vehicle Theft", "Contents": "Home Invasion"}.get(item_type, "Other")

def peak_window(g):
    # Use only reliable timestamps. Return the dominant day + 3-hour block.
    r = g[g["time_reliable"] == True]
    if len(r) == 0:
        return None, "none"
    dow = r["INCIDENT_DATE_TIME"].dt.dayofweek
    day = int(dow.mode().iloc[0])
    day_rows = r[dow == day]
    hours = day_rows["INCIDENT_DATE_TIME"].dt.hour
    hr = int(hours.mode().iloc[0]) if len(hours.mode()) else 12
    # Confidence: high if we have a decent number of reliable rows
    confidence = "high" if len(r) >= 30 else "low"
    window = {"day": DAYS[day], "start": f"{hr:02d}:00", "end": f"{(hr+3)%24:02d}:00"}
    return window, confidence

def enrich():
    df = pd.read_csv(config.CURATED_DIR / "step4_pilot.csv",
                     parse_dates=["INCIDENT_DATE_TIME"])
    geo = pd.read_csv(config.CURATED_DIR / "step5_geocoded.csv")
    geo_map = {r["SUBURB_CLEAN"]: r for _, r in geo.iterrows()}

    df["incident_type"] = df["ITEM_TYPE"].map(incident_type)
    latest = df["INCIDENT_DATE_TIME"].max()

    g = df.groupby("SUBURB_CLEAN")
    freq = g.size()
    cost = df[df["CLAIM_AMOUNT"] > 0].groupby("SUBURB_CLEAN")["CLAIM_AMOUNT"].sum()
    latest_per = g["INCIDENT_DATE_TIME"].max()
    days_since = (latest - latest_per).dt.days

    c_freq = normalize(np.log1p(freq))
    c_sev = normalize(np.log1p(cost))
    c_rec = normalize(np.exp(-days_since / 365.0))
    rel = df[df["time_reliable"] == True]
    peakcnt = rel[rel["INCIDENT_DATE_TIME"].dt.hour.isin(PEAK_HOURS)].groupby("SUBURB_CLEAN").size()
    relcnt = rel.groupby("SUBURB_CLEAN").size()
    c_peak = normalize((peakcnt / relcnt).fillna(0.0))

    w = config.WEIGHTS
    hotspots = []
    for sub in freq.index:
        sub_rows = df[df["SUBURB_CLEAN"] == sub]
        window, wconf = peak_window(sub_rows)
        gm = geo_map[sub]
        score = round(100 * (
            w["frequency"] * c_freq[sub] +
            w["severity"] * c_sev.get(sub, 0) +
            w["recency"] * c_rec[sub] +
            w["peak_time"] * c_peak.get(sub, 0)
        ), 1)
        hotspots.append({
            "hotspot_id": None,  # assigned after sort
            "name": sub.title(),
            "latitude": round(float(gm["lat"]), 4),
            "longitude": round(float(gm["lon"]), 4),
            "metro": gm["METRO"],
            "risk_score": score,
            "frequency_score": round(float(c_freq[sub]), 2),
            "severity_score": round(float(c_sev.get(sub, 0)), 2),
            "recency_score": round(float(c_rec[sub]), 2),
            "peak_time_score": round(float(c_peak.get(sub, 0)), 2),
            "main_peril": sub_rows["incident_type"].mode().iloc[0],
            "peak_window": window,
            "peak_window_confidence": wconf,
            "claim_count": int(freq[sub]),
            "total_claim_value": round(float(cost.get(sub, 0)), 0),
            "formula_version": config.FORMULA_VERSION,
        })

    hotspots.sort(key=lambda h: h["risk_score"], reverse=True)
    for i, h in enumerate(hotspots, 1):
        h["hotspot_id"] = f"H{i:03d}"

    out = config.CURATED_DIR / "hotspots.json"
    out.write_text(json.dumps(hotspots, indent=2), encoding="utf-8")
    print(f"Wrote {len(hotspots)} hotspots -> {out.name}")
    for h in hotspots[:5]:
        print(f"  {h['hotspot_id']} {h['name']:22s} {h['risk_score']:5.1f}  "
              f"{h['main_peril']:14s} {h['peak_window']['day']} {h['peak_window']['start']}")

if __name__ == "__main__":
    enrich()