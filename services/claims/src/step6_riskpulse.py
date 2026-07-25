# Step 6: Risk Pulse. Compute a 0-100 priority score per suburb,
# broken into 4 explainable components. Handbook page 11.
# Reads:  data/curated/step4_pilot.csv, data/curated/step5_geocoded.csv
# Writes: data/curated/step6_hotspots.csv
#
# This is a PRIORITY score describing observed claims, NOT a crime probability.

import numpy as np
import pandas as pd
import config

# For peak-time: define the operational window we care about.
# Evening hours are a common risk window; configurable.
PEAK_HOURS = range(18, 24)  # 6pm-midnight

def normalize(series):
    # Min-max to 0..1 within the pilot context. Flat series -> 0.
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.0, index=series.index)
    return (series - lo) / (hi - lo)

def risk_pulse():
    df = pd.read_csv(config.CURATED_DIR / "step4_pilot.csv",
                     parse_dates=["INCIDENT_DATE_TIME"])
    geo = pd.read_csv(config.CURATED_DIR / "step5_geocoded.csv")

    latest_date = df["INCIDENT_DATE_TIME"].max()

    # --- Aggregate per suburb ---
    agg = df.groupby("SUBURB_CLEAN").agg(
        incident_count=("Incident", "count"),
        total_cost=("CLAIM_AMOUNT", lambda s: s[s > 0].sum()),
        latest=("INCIDENT_DATE_TIME", "max"),
    )

    # Peak-time share: only reliable timestamps count
    reliable = df[df["time_reliable"] == True]
    peak = reliable[reliable["INCIDENT_DATE_TIME"].dt.hour.isin(PEAK_HOURS)]
    peak_counts = peak.groupby("SUBURB_CLEAN").size()
    reliable_counts = reliable.groupby("SUBURB_CLEAN").size()
    agg["peak_share"] = (peak_counts / reliable_counts).fillna(0.0)

    # --- Raw components ---
    agg["c_frequency"] = np.log1p(agg["incident_count"])
    agg["c_severity"] = np.log1p(agg["total_cost"])
    days_since = (latest_date - agg["latest"]).dt.days
    agg["c_recency"] = np.exp(-days_since / 365.0)
    agg["c_peak"] = agg["peak_share"]

    # --- Normalize each component 0..1 within pilot ---
    freq_n = normalize(agg["c_frequency"])
    sev_n = normalize(agg["c_severity"])
    rec_n = normalize(agg["c_recency"])
    peak_n = normalize(agg["c_peak"])

    w = config.WEIGHTS
    agg["risk_score"] = (
        100 * (
            w["frequency"] * freq_n +
            w["severity"] * sev_n +
            w["recency"] * rec_n +
            w["peak_time"] * peak_n
        )
    ).round(1)

    # Keep normalized components visible for explainability
    agg["norm_frequency"] = freq_n.round(3)
    agg["norm_severity"] = sev_n.round(3)
    agg["norm_recency"] = rec_n.round(3)
    agg["norm_peak"] = peak_n.round(3)
    agg["formula_version"] = config.FORMULA_VERSION

    # --- Join coordinates ---
    result = agg.reset_index().merge(
        geo[["SUBURB_CLEAN", "METRO", "lat", "lon"]],
        on="SUBURB_CLEAN", how="left"
    )
    result = result.sort_values("risk_score", ascending=False)

    out = config.CURATED_DIR / "step6_hotspots.csv"
    result.to_csv(out, index=False)

    print("=== RISK PULSE: pilot hotspots ranked ===")
    cols = ["SUBURB_CLEAN", "METRO", "incident_count", "total_cost", "risk_score"]
    print(result[cols].to_string(index=False))
    print(f"\nSaved: {out.name}")

if __name__ == "__main__":
    risk_pulse()
    