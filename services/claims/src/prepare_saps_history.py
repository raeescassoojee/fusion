"""Extend the existing SAPS history through 2024/25 without changing live scores."""

import pandas as pd

import config

BASE_HISTORY = config.PARTNER_DIR / "sapacr-2008-2023-v1.1.csv"
ANNUAL_DIR = config.PARTNER_DIR / "annual"
LATEST_ANNUAL = ANNUAL_DIR / "2024-2025 _Annual_Financial year_WEB (1).xlsx"
OUTPUT_HISTORY = config.PARTNER_DIR / "saps_history_2008_2025.csv"
OUTPUT_LONG = config.PARTNER_DIR / "saps_annual_2019_2025_by_station_crime.csv"
AUDIT_FILE = config.CURATED_DIR / "saps_annual_audit.csv"

YEARS = ["2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024", "2024-2025"]
CATEGORY_TO_FIELD = {
    "Burglary at residential premises": "burglary_res",
    "Robbery at residential premises": "robbery_res",
    "Theft of motor vehicle and motorcycle": "vehicle_theft",
    "Carjacking": "carjacking",
    "Theft out of or from motor vehicle": "theft_from_vehicle",
}


def _normalise_year(value):
    return str(value).replace("-", "/")


def prepare():
    annual = pd.read_excel(LATEST_ANNUAL, sheet_name="RAW Data", header=2)
    annual = annual[annual["Comp level"].astype(str).str.strip().str.casefold().eq("station")].copy()
    annual["Station"] = annual["Station"].astype(str).str.upper().str.strip()
    annual["Crime_Category"] = annual["Crime_Category"].astype(str).str.strip()

    duplicate_keys = int(annual.duplicated(["Station", "Crime_Category"]).sum())
    if duplicate_keys:
        raise ValueError(f"Annual SAPS data has {duplicate_keys} duplicate station/category keys")

    long = annual.melt(
        id_vars=["Station", "District", "Province", "Crime_Category"],
        value_vars=YEARS, var_name="Year", value_name="Incidents",
    )
    long["Year"] = long["Year"].map(_normalise_year)
    long["Incidents"] = pd.to_numeric(long["Incidents"], errors="raise").astype(int)
    # Official historical workbooks occasionally contain a negative correction
    # (three rows in the supplied national archive). A negative incident count is
    # not a valid model input, so retain the immutable source workbook and clamp
    # only the prepared feature layer. The correction count is written to audit.
    negative_corrections = int((long["Incidents"] < 0).sum())
    long["Incidents"] = long["Incidents"].clip(lower=0)
    long.to_csv(OUTPUT_LONG, index=False)

    base = pd.read_csv(BASE_HISTORY, encoding="cp1252")
    base["station"] = base["station"].astype(str).str.lower().str.strip()
    base["year"] = base["year"].map(_normalise_year)
    risk_fields = list(CATEGORY_TO_FIELD.values())
    base_negative_corrections = int(
        (base[risk_fields].apply(pd.to_numeric, errors="coerce") < 0).sum().sum()
    )
    base[risk_fields] = base[risk_fields].apply(pd.to_numeric, errors="coerce").clip(lower=0)

    added = long[
        long["Year"].isin(["2023/2024", "2024/2025"])
        & long["Crime_Category"].isin(CATEGORY_TO_FIELD)
    ].copy()
    added["field"] = added["Crime_Category"].map(CATEGORY_TO_FIELD)
    wide = (
        added.pivot(index=["Year", "Station", "District"], columns="field", values="Incidents")
        .reset_index()
        .rename(columns={"Year": "year", "Station": "station", "District": "dc_mn"})
    )
    wide.columns.name = None
    wide["station"] = wide["station"].str.lower()
    wide["dc_mn"] = wide["dc_mn"].astype(str).str.lower()
    wide["loc_mn"] = wide["dc_mn"]

    station_geo = base.sort_values("year").drop_duplicates("station", keep="last").set_index("station")[["longitude", "latitude"]]
    wide = wide.join(station_geo, on="station")
    for col in base.columns:
        if col not in wide.columns:
            wide[col] = 0
    wide = wide[base.columns]

    combined = pd.concat([base, wide], ignore_index=True)
    combined = combined.drop_duplicates(["year", "station"], keep="last").sort_values(["station", "year"])
    combined.to_csv(OUTPUT_HISTORY, index=False)

    pilot = {station.upper() for station in config.SUBURB_TO_STATION.values()}
    available_pilot = set(annual["Station"]) & pilot
    audit = pd.DataFrame([
        {"check": "station_rows", "value": len(annual), "status": "PASS"},
        {"check": "stations", "value": annual["Station"].nunique(), "status": "PASS"},
        {"check": "crime_categories", "value": annual["Crime_Category"].nunique(), "status": "PASS"},
        {"check": "duplicate_station_category_keys", "value": duplicate_keys, "status": "PASS"},
        {"check": "annual_negative_corrections_clipped", "value": negative_corrections, "status": "PASS"},
        {"check": "base_negative_corrections_clipped", "value": base_negative_corrections, "status": "PASS"},
        {"check": "pilot_stations_found", "value": len(available_pilot), "status": "PASS" if available_pilot == pilot else "FAIL"},
        {"check": "historical_years", "value": combined["year"].nunique(), "status": "PASS"},
        {"check": "historical_start", "value": combined["year"].min(), "status": "PASS"},
        {"check": "historical_end", "value": combined["year"].max(), "status": "PASS"},
    ])
    audit.to_csv(AUDIT_FILE, index=False)
    if available_pilot != pilot:
        raise ValueError(f"Missing pilot stations: {sorted(pilot - available_pilot)}")
    print(f"Prepared {len(long):,} annual station/category/year rows and {len(combined):,} historical station/year rows through 2024/25.")


if __name__ == "__main__":
    prepare()
