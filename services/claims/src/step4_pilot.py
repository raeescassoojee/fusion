# Step 4: Keep only pilot-suburb rows. Attach metro label.
# Reads:  data/curated/step3_clean.csv
# Writes: data/curated/step4_pilot.csv

import pandas as pd
import config

def select_pilot():
    df = pd.read_csv(config.CURATED_DIR / "step3_clean.csv",
                     parse_dates=["INCIDENT_DATE_TIME"])

    pilot_names = set(config.PILOT_SUBURBS.keys())
    pilot = df[df["SUBURB_CLEAN"].isin(pilot_names)].copy()

    # Attach metro label from config
    pilot["METRO"] = pilot["SUBURB_CLEAN"].map(
        lambda s: config.PILOT_SUBURBS[s]["metro"]
    )

    out = config.CURATED_DIR / "step4_pilot.csv"
    pilot.to_csv(out, index=False)

    print(f"Pilot rows: {len(pilot)}")
    print("\nRows per suburb:")
    print(pilot.groupby(["METRO", "SUBURB_CLEAN"]).size().to_string())
    print(f"\nSaved: {out.name}")

if __name__ == "__main__":
    select_pilot()