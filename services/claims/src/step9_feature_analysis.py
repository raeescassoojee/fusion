# Step 9: Explain and grade the risk scores. Does NOT change the scores.
# Adds per hotspot: contributions (why it scored that), confidence (how solid).
# Prints a weight-sensitivity check (is the ranking robust?).
# Reads:  data/curated/hotspots.json
# Writes: data/curated/hotspots.json (enriched in place)
#         data/curated/sensitivity_report.txt

import json
import pandas as pd
import config

COMPONENTS = [
    ("frequency_score", "frequency"),
    ("severity_score", "severity"),
    ("recency_score", "recency"),
    ("peak_time_score", "peak_time"),
]

def reliable_share():
    # Timestamp reliability per suburb, from the pilot data.
    df = pd.read_csv(config.CURATED_DIR / "step4_pilot.csv")
    g = df.groupby("SUBURB_CLEAN")
    share = (g["time_reliable"].sum() / g.size())
    return {k.title(): float(v) for k, v in share.items()}

def analyse():
    path = config.CURATED_DIR / "hotspots.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    hotspots = data if isinstance(data, list) else data["hotspots"]

    w = config.WEIGHTS
    rel_share = reliable_share()
    max_count = max(h["claim_count"] for h in hotspots)

    for h in hotspots:
        # --- Contributions: share of score from each component ---
        total = sum(h[ck] * w[wk] for ck, wk in COMPONENTS)
        contribs = {wk: round(100 * (h[ck] * w[wk]) / total, 1)
                    for ck, wk in COMPONENTS} if total > 0 else {}
        h["contributions_pct"] = contribs
        h["primary_driver"] = max(contribs, key=contribs.get) if contribs else None

        # --- Confidence: volume + timestamp reliability + SAPS corroboration ---
        vol = h["claim_count"] / max_count
        rel = rel_share.get(h["name"], 0.9)
        corr = 1.0 if h.get("saps", {}).get("corroborated") else 0.0
        score = round(100 * (0.5 * vol + 0.3 * rel + 0.2 * corr))
        band = "HIGH" if score >= 70 else "MEDIUM" if score >= 45 else "LOW"
        h["confidence"] = {"score": score, "band": band,
                           "inputs": {"volume": round(vol, 2),
                                      "timestamp_reliability": round(rel, 2),
                                      "saps_corroborated": bool(corr)}}

    # --- Sensitivity: does the ranking survive different weights? ---
    def rank(weights):
        scored = [(h["name"], sum(h[ck] * weights[wk] for ck, wk in COMPONENTS))
                  for h in hotspots]
        return [n for n, _ in sorted(scored, key=lambda x: -x[1])]

    base = rank(w)
    equal = rank({k: 0.25 for k in ["frequency", "severity", "recency", "peak_time"]})
    sev_led = rank({"frequency": 0.30, "severity": 0.45, "recency": 0.15, "peak_time": 0.10})
    top3_equal = len(set(base[:3]) & set(equal[:3]))
    top3_sev = len(set(base[:3]) & set(sev_led[:3]))

    report = [
        "WEIGHT SENSITIVITY REPORT",
        f"Base weights ({w}):",
        f"  {base}",
        f"Equal weights:  {equal}",
        f"Severity-led:   {sev_led}",
        f"Top-3 overlap: {top3_equal}/3 (equal), {top3_sev}/3 (severity-led)",
        "",
        "Interpretation: high top-3 overlap means the ranking is robust to weight choice.",
    ]
    (config.CURATED_DIR / "sensitivity_report.txt").write_text("\n".join(report), encoding="utf-8")

    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print("Feature analysis complete.")
    print(f"{'Suburb':16s} {'score':>6s} {'driver':10s} {'conf':>4s} {'band':6s}")
    for h in hotspots:
        print(f"{h['name']:16s} {h['risk_score']:6.1f} {h['primary_driver']:10s} "
              f"{h['confidence']['score']:4.0f} {h['confidence']['band']:6s}")
    print("\nSensitivity: top-3 overlap "
          f"{top3_equal}/3 (equal), {top3_sev}/3 (severity-led). See sensitivity_report.txt")

if __name__ == "__main__":
    analyse()