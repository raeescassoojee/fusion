# Step 3: Clean the data. Quarantine unusable rows, never delete silently.
# Reads:  data/curated/step1_ingested.csv
# Writes: data/curated/step3_clean.csv         (rows safe to map/score)
#         data/curated/step3_quarantine.csv    (rows held back, with reason)
#
# NOTE for teammates: pandas reads the text "NULL" as NaN on CSV load,
# so a missing suburb is detected with .isna(), not == "NULL".

import pandas as pd
import config

def clean():
    df = pd.read_csv(config.CURATED_DIR / "step1_ingested.csv",
                     parse_dates=["INCIDENT_DATE_TIME"])

    # Keep original suburb for audit, add a cleaned version
    df["SUBURB_RAW"] = df["SUBURB"]
    df["SUBURB_CLEAN"] = df["SUBURB"].dropna().astype(str).str.strip().str.upper()

    # Numeric amount, non-numeric becomes NaN
    df["CLAIM_AMOUNT"] = pd.to_numeric(df["CLAIM_AMOUNT"], errors="coerce")

    # Flags (not deletions)
    df["amount_valid"] = df["CLAIM_AMOUNT"] > 0
    df["time_reliable"] = ~((df["INCIDENT_DATE_TIME"].dt.hour == 0) &
                            (df["INCIDENT_DATE_TIME"].dt.minute == 0))

    # Quarantine: rows we cannot place on a map (no suburb)
    no_suburb = df["SUBURB_CLEAN"].isna()
    quarantine = df[no_suburb].copy()
    quarantine["quarantine_reason"] = "missing_suburb"

    clean_df = df[~no_suburb].copy()

    # Save both
    clean_path = config.CURATED_DIR / "step3_clean.csv"
    quar_path = config.CURATED_DIR / "step3_quarantine.csv"
    clean_df.to_csv(clean_path, index=False)
    quarantine.to_csv(quar_path, index=False)

    print(f"Clean rows (mappable):   {len(clean_df)}")
    print(f"Quarantined rows:        {len(quarantine)}")
    print(f"  bad/zero amounts still kept but flagged: {(~clean_df['amount_valid']).sum()}")
    print(f"  unreliable timestamps flagged:           {(~clean_df['time_reliable']).sum()}")
    print(f"Saved: {clean_path.name}, {quar_path.name}")

if __name__ == "__main__":
    clean()