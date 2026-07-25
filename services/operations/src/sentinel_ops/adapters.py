from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sentinel_ops.models import CameraEvent


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output.columns = [
        str(column).strip().lower().replace(" ", "_").replace("/", "_")
        for column in output.columns
    ]
    return output


def load_discovery_claims(path: str | Path) -> pd.DataFrame:
    frame = normalise_columns(pd.read_excel(path, engine="openpyxl"))
    expected = {
        "incident",
        "peril",
        "suburb",
        "item_type",
        "incident_date_time",
        "claim_amount",
    }
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"Discovery workbook missing columns: {sorted(missing)}")
    frame["suburb"] = (
        frame["suburb"]
        .astype("string")
        .str.strip()
        .str.upper()
        .replace({"NULL": pd.NA})
    )
    frame["incident_date_time"] = pd.to_datetime(
        frame["incident_date_time"],
        errors="coerce",
    )
    frame["claim_amount"] = pd.to_numeric(
        frame["claim_amount"],
        errors="coerce",
    )
    return frame


def profile_discovery_claims(path: str | Path) -> dict:
    frame = load_discovery_claims(path)
    return {
        "rows": int(len(frame)),
        "columns": list(frame.columns),
        "missing_suburb": int(frame["suburb"].isna().sum()),
        "date_min": frame["incident_date_time"].min().isoformat(),
        "date_max": frame["incident_date_time"].max().isoformat(),
        "total_claim_amount": float(frame["claim_amount"].fillna(0).sum()),
    }


def _read_tabular(path: str | Path) -> pd.DataFrame:
    return (
        pd.read_csv(path)
        if str(path).lower().endswith(".csv")
        else pd.read_excel(path)
    )


def load_saps_cleaned(path: str | Path) -> pd.DataFrame:
    frame = normalise_columns(_read_tabular(path))
    required = {"station", "crime_category", "count"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"SAPS cleaned table missing columns: {sorted(missing)}")
    frame["station"] = (
        frame["station"].astype("string").str.strip().str.upper()
    )
    frame["crime_category"] = (
        frame["crime_category"].astype("string").str.strip().str.lower()
    )
    frame["count"] = pd.to_numeric(frame["count"], errors="coerce").fillna(0)
    return frame


def load_stats_sa_exposure(path: str | Path) -> pd.DataFrame:
    frame = normalise_columns(_read_tabular(path))
    required = {"area", "population"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Stats SA table missing columns: {sorted(missing)}")
    frame["area"] = frame["area"].astype("string").str.strip().str.upper()
    frame["population"] = pd.to_numeric(
        frame["population"],
        errors="coerce",
    ).fillna(0)
    return frame


def load_partner_events(
    path: str | Path,
    source_name: str,
) -> list[CameraEvent]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        CameraEvent.model_validate({**row, "source": source_name})
        for row in data
    ]
