"""Add historical context without changing claims or current SAPS scores."""

import json
import pandas as pd
import config

HISTORICAL_FILE = config.PARTNER_DIR / "saps_history_2008_2025.csv"
TREND_FIELDS = ["burglary_res", "robbery_res", "vehicle_theft", "carjacking", "theft_from_vehicle"]


def add_history():
    path = config.CURATED_DIR / "hotspots.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    hotspots = data if isinstance(data, list) else data["hotspots"]
    hist = pd.read_csv(HISTORICAL_FILE, encoding="cp1252")
    hist["STATION"] = hist["station"].astype(str).str.upper().str.strip()
    # Keep official correction rows in the supplied source, but never allow a
    # negative count to reduce an operational risk feature.
    hist[TREND_FIELDS] = hist[TREND_FIELDS].apply(pd.to_numeric, errors="coerce").clip(lower=0)
    hist["risk_total"] = hist[TREND_FIELDS].fillna(0).sum(axis=1)

    rows = []
    for h in hotspots:
        station = config.SUBURB_TO_STATION.get(h["name"].upper())
        station_hist = hist[hist["STATION"].eq(station)].sort_values("year")
        if station_hist.empty:
            block = {"station": station.title() if station else None, "available": False}
        else:
            last = station_hist.iloc[-1]
            recent = station_hist.tail(5)
            first_recent = float(recent.iloc[0]["risk_total"])
            latest = float(last["risk_total"])
            change = round(100 * (latest - first_recent) / first_recent, 1) if first_recent else None
            trend = "INCREASING" if change is not None and change > 5 else "DECREASING" if change is not None and change < -5 else "STABLE"
            block = {
                "station": station.title(), "available": True,
                "latest_historical_year": str(last["year"]),
                "latest_historical_total": int(latest),
                "five_year_average": round(float(recent["risk_total"].mean()), 1),
                "five_year_change_pct": change, "trend": trend,
                "years_available": int(len(station_hist)),
                "note": "Historical context through 2024/25 only; not included in the current blended score.",
            }
        h["saps_historical"] = block
        rows.append({"suburb": h["name"], **block})

    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(config.CURATED_DIR / "saps_historical_summary.csv", index=False)
    print(f"Added historical SAPS context to {len(hotspots)} hotspots.")


if __name__ == "__main__":
    add_history()
