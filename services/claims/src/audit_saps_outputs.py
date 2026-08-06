"""Validate the prepared SAPS files and their live hotspot integration."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

import config


CURRENT = config.PARTNER_DIR / "saps_2025_2026_by_station_crime.csv"
HISTORY = config.PARTNER_DIR / "saps_history_2008_2025.csv"
ANNUAL_LONG = config.PARTNER_DIR / "saps_annual_2019_2025_by_station_crime.csv"
HOTSPOTS = config.CURATED_DIR / "hotspots.json"
OUTPUT = config.CURATED_DIR / "saps_integration_audit.json"
RISK_FIELDS = ["burglary_res", "robbery_res", "vehicle_theft", "carjacking", "theft_from_vehicle"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(output: Path = OUTPUT) -> dict[str, object]:
    current = pd.read_csv(CURRENT)
    history = pd.read_csv(HISTORY, encoding="cp1252")
    annual = pd.read_csv(ANNUAL_LONG)
    hotspots = json.loads(HOTSPOTS.read_text(encoding="utf-8"))
    pilot = {station.upper() for station in config.SUBURB_TO_STATION.values()}
    current_stations = set(current["Station"].astype(str).str.upper().str.strip())
    history_stations = set(history["station"].astype(str).str.upper().str.strip())
    history_year_counts = history.groupby(history["station"].astype(str).str.casefold().str.strip())["year"].nunique()

    checks = {
        "current_station_crime_rows": int(len(current)),
        "current_stations": int(len(current_stations)),
        "current_categories": int(current["Crime_Category"].nunique()),
        "current_duplicate_station_category_keys": int(current.duplicated(["Station", "Crime_Category"]).sum()),
        "current_null_incidents": int(pd.to_numeric(current["Incidents"], errors="coerce").isna().sum()),
        "current_negative_incidents": int((pd.to_numeric(current["Incidents"], errors="coerce") < 0).sum()),
        "pilot_stations_in_current": int(len(pilot & current_stations)),
        "history_rows": int(len(history)),
        "history_stations": int(len(history_stations)),
        "history_years": int(history["year"].nunique()),
        "history_duplicate_station_year_keys": int(history.duplicated(["station", "year"]).sum()),
        "history_source_negative_corrections": int((history[RISK_FIELDS].apply(pd.to_numeric, errors="coerce") < 0).sum().sum()),
        "pilot_stations_in_history": int(len(pilot & history_stations)),
        "pilot_stations_with_17_years": int(sum(history_year_counts.get(station.casefold(), 0) == 17 for station in pilot)),
        "annual_long_rows": int(len(annual)),
        "annual_long_duplicate_keys": int(annual.duplicated(["Station", "Crime_Category", "Year"]).sum()),
        "annual_long_source_negative_corrections": int((pd.to_numeric(annual["Incidents"], errors="coerce") < 0).sum()),
        "runtime_hotspots": int(len(hotspots)),
        "runtime_current_year_hotspots": int(sum((item.get("saps_typed") or {}).get("year") == "2025/2026" for item in hotspots)),
        "runtime_historical_context_hotspots": int(sum(bool((item.get("saps_historical") or {}).get("available")) for item in hotspots)),
    }
    failures = []
    expected_zero = [
        "current_duplicate_station_category_keys",
        "current_null_incidents",
        "current_negative_incidents",
        "history_duplicate_station_year_keys",
        "annual_long_duplicate_keys",
    ]
    failures.extend(key for key in expected_zero if checks[key] != 0)
    if checks["pilot_stations_in_current"] != len(pilot):
        failures.append("pilot_stations_in_current")
    if checks["pilot_stations_with_17_years"] != len(pilot):
        failures.append("pilot_stations_with_17_years")
    if checks["runtime_current_year_hotspots"] != len(hotspots):
        failures.append("runtime_current_year_hotspots")
    if checks["runtime_historical_context_hotspots"] != len(hotspots):
        failures.append("runtime_historical_context_hotspots")

    result: dict[str, object] = {
        "status": "PASS" if not failures else "FAIL",
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "failed_checks": failures,
        "source_hashes": {
            "saps_2025_2026_by_station_crime.csv": _sha256(CURRENT),
            "saps_history_2008_2025.csv": _sha256(HISTORY),
            "saps_annual_2019_2025_by_station_crime.csv": _sha256(ANNUAL_LONG),
            "hotspots.json": _sha256(HOTSPOTS),
        },
        "correction_policy": (
            "Official negative corrections remain in immutable prepared source files; "
            "operational and training feature layers clip counts at zero and record the count."
        ),
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if failures:
        raise ValueError(f"SAPS integration audit failed: {', '.join(failures)}")
    return result


if __name__ == "__main__":
    report = audit()
    print(f"SAPS integration audit: {report['status']}")
    for key, value in report["checks"].items():
        print(f"  {key}: {value}")
    print(f"  output: {OUTPUT}")
