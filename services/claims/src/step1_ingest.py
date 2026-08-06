# Step 1: Load the raw workbook into a dataframe and save a clean copy.
# Reads:  data/raw/claims_raw.xlsx
# Writes: data/curated/step1_ingested.csv

import pandas as pd
import config

def ingest():
    print(f"Reading: {config.RAW_FILE}")
    df = pd.read_excel(config.RAW_FILE)

    print(f"Rows loaded: {len(df)}")
    print(f"Columns: {list(df.columns)}")

    config.CURATED_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CURATED_DIR / "step1_ingested.csv"
    df.to_csv(out, index=False)
    print(f"Saved: {out}")

    return df

if __name__ == "__main__":
    ingest()