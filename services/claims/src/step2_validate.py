# Step 2: Measure data-quality issues. Does NOT modify data.
# Reads:  data/curated/step1_ingested.csv
# Writes: data/curated/step2_report.txt

import pandas as pd
import config

EXPECTED_COLS = [
    "Incident", "PERIL", "SUBURB", "ITEM_TYPE", "VEHICLE_MAKE",
    "VEHICLE_MODEL", "VEHICLE_YEAR", "INCIDENT_DATE_TIME",
    "CLAIM_AMOUNT", "ITEM_CATEGORY", "ITEM_PERIL_DESCR",
]

def validate():
    df = pd.read_csv(config.CURATED_DIR / "step1_ingested.csv",
                     parse_dates=["INCIDENT_DATE_TIME"])
    lines = []

    def log(msg):
        print(msg)
        lines.append(msg)

    log("=== DATA QUALITY REPORT ===")
    log(f"Total rows: {len(df)}")

    missing_cols = [c for c in EXPECTED_COLS if c not in df.columns]
    log(f"Missing expected columns: {missing_cols or 'none'}")

    # Suburb issues
    # Suburb issues (pandas reads "NULL" text as NaN on load)
    missing_suburb = df["SUBURB"].isna().sum()
    clean = df["SUBURB"].dropna().astype(str).str.strip().str.upper()
    unique_suburb = clean.nunique()
    log(f"Missing suburb (was 'NULL' or blank): {missing_suburb}")
    log(f"Unique clean suburbs: {unique_suburb}")

    # Amount issues
    amt = pd.to_numeric(df["CLAIM_AMOUNT"], errors="coerce")
    log(f"Negative amounts: {(amt < 0).sum()}")
    log(f"Zero amounts: {(amt == 0).sum()}")
    log(f"Non-numeric amounts: {amt.isna().sum()}")

    # Timestamp issues
    dt = df["INCIDENT_DATE_TIME"]
    midnight = ((dt.dt.hour == 0) & (dt.dt.minute == 0)).sum()
    log(f"Midnight timestamps (time may be unreliable): {midnight}")
    log(f"Date range: {dt.min()} -> {dt.max()}")

    out = config.CURATED_DIR / "step2_report.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    log(f"\nSaved report: {out}")

if __name__ == "__main__":
    validate()