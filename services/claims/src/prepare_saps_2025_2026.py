"""Build one audited 2025/2026 station/crime file from the four SAPS quarters."""

from pathlib import Path
import re
import pandas as pd
import config

YEAR = "2025/2026"


def prepare():
    files = sorted((config.PARTNER_DIR / "quarterly").glob("2025-2026_*Quarter_WEB.xlsx"))
    if len(files) != 4:
        raise FileNotFoundError(f"Expected 4 quarterly workbooks, found {len(files)}")

    frames = []
    audit = []
    for path in files:
        df = pd.read_excel(path, sheet_name="RAW Data", header=2)
        df = df[df["Comp level"].astype(str).str.strip().eq("Station")].copy()
        total_cols = [c for c in df.columns if isinstance(c, str) and " to " in c]
        total_cols = [c for c in total_cols if re.findall(r"20\d{2}", c)]
        latest_year = max(int(y) for c in total_cols for y in re.findall(r"20\d{2}", c))
        total_cols = [c for c in total_cols if int(re.findall(r"20\d{2}", c)[-1]) == latest_year]
        if len(total_cols) != 1:
            raise ValueError(f"Could not identify current-quarter total in {path.name}: {total_cols}")
        total_col = total_cols[0]
        out = df[["Station", "Crime_Category"]].copy()
        out["Incidents"] = pd.to_numeric(df[total_col], errors="raise").astype(int)
        out["Quarter_Source"] = path.name
        frames.append(out)
        audit.append({"file": path.name, "station_crime_rows": len(out), "incidents": int(out["Incidents"].sum())})

    quarterly = pd.concat(frames, ignore_index=True)
    annual = quarterly.groupby(["Station", "Crime_Category"], as_index=False)["Incidents"].sum()
    annual["Year"] = YEAR
    annual = annual.sort_values(["Station", "Crime_Category"])
    out_path = config.PARTNER_DIR / "saps_2025_2026_by_station_crime.csv"
    annual.to_csv(out_path, index=False)
    pd.DataFrame(audit).to_csv(config.CURATED_DIR / "saps_quarterly_audit.csv", index=False)
    print(f"Prepared {len(annual):,} annual station/crime rows -> {out_path.name}")
    return out_path


if __name__ == "__main__":
    prepare()
