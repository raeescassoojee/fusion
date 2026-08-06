"""Train an auditable next-year SAPS station-risk forecast.

This model predicts the next financial year's combined count for five safety
categories using only earlier years from the same station. It is a patrol
planning signal, not a prediction about a person and not a crime accusation.

The final three target years form an untouched chronological test set (roughly
80/20). A random split is deliberately avoided because it would leak future
conditions into a historical forecasting benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer


CLAIMS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = CLAIMS_ROOT / "data" / "partner" / "saps_history_2008_2025.csv"
DEFAULT_CURRENT = CLAIMS_ROOT / "data" / "partner" / "saps_2025_2026_by_station_crime.csv"
DEFAULT_OUTPUT = CLAIMS_ROOT / "data" / "model" / "hotspot_forecast"

RISK_FIELDS = [
    "burglary_res",
    "robbery_res",
    "vehicle_theft",
    "carjacking",
    "theft_from_vehicle",
]
CATEGORY_TO_FIELD = {
    "Burglary at residential premises": "burglary_res",
    "Robbery at residential premises": "robbery_res",
    "Theft of motor vehicle and motorcycle": "vehicle_theft",
    "Carjacking": "carjacking",
    "Theft out of or from motor vehicle": "theft_from_vehicle",
}
FEATURES = [
    "risk_total_lag_1",
    "risk_total_lag_2",
    "risk_total_lag_3",
    "rolling_3_mean",
    "rolling_3_std",
    "trend_1_year",
    "long_term_mean",
    "year_index",
    *[f"{field}_lag_1" for field in RISK_FIELDS],
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _year_start(value: object) -> int:
    text = str(value).strip().replace("-", "/")
    return int(text.split("/", 1)[0])


def _prepare_history(path: Path) -> tuple[pd.DataFrame, int]:
    frame = pd.read_csv(path, encoding="cp1252")
    required = {"year", "station", *RISK_FIELDS}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"SAPS history is missing columns: {', '.join(missing)}")

    frame["station"] = frame["station"].astype(str).str.casefold().str.strip()
    frame["year_start"] = frame["year"].map(_year_start)
    duplicate_count = int(frame.duplicated(["station", "year_start"]).sum())
    if duplicate_count:
        raise ValueError(f"SAPS history has {duplicate_count} duplicate station/year keys")

    numeric = frame[RISK_FIELDS].apply(pd.to_numeric, errors="coerce")
    correction_count = int((numeric < 0).sum().sum())
    frame[RISK_FIELDS] = numeric.clip(lower=0).fillna(0)
    frame["risk_total"] = frame[RISK_FIELDS].sum(axis=1)
    frame = frame.sort_values(["station", "year_start"]).reset_index(drop=True)
    grouped = frame.groupby("station", sort=False)

    for lag in (1, 2, 3):
        frame[f"risk_total_lag_{lag}"] = grouped["risk_total"].shift(lag)
    for field in RISK_FIELDS:
        frame[f"{field}_lag_1"] = grouped[field].shift(1)

    frame["rolling_3_mean"] = frame[
        ["risk_total_lag_1", "risk_total_lag_2", "risk_total_lag_3"]
    ].mean(axis=1)
    frame["rolling_3_std"] = frame[
        ["risk_total_lag_1", "risk_total_lag_2", "risk_total_lag_3"]
    ].std(axis=1)
    frame["trend_1_year"] = frame["risk_total_lag_1"] - frame["risk_total_lag_2"]
    frame["long_term_mean"] = grouped["risk_total"].transform(
        lambda values: values.shift(1).expanding(min_periods=1).mean()
    )
    frame["year_index"] = frame["year_start"] - int(frame["year_start"].min())
    eligible = frame.dropna(subset=["risk_total_lag_1", "risk_total_lag_2", "risk_total_lag_3"]).copy()
    return eligible, correction_count


def _metrics(actual: pd.Series | np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual_array = np.asarray(actual, dtype=np.float64)
    predicted_array = np.asarray(predicted, dtype=np.float64)
    absolute_error = np.abs(actual_array - predicted_array)
    denominator = float(np.abs(actual_array).sum())
    return {
        "mae": round(float(mean_absolute_error(actual_array, predicted_array)), 4),
        "rmse": round(float(mean_squared_error(actual_array, predicted_array) ** 0.5), 4),
        "r2": round(float(r2_score(actual_array, predicted_array)), 6),
        "wape": round(float(absolute_error.sum() / denominator), 6) if denominator else 0.0,
    }


def _current_observed(path: Path) -> pd.Series:
    current = pd.read_csv(path)
    current["Station"] = current["Station"].astype(str).str.casefold().str.strip()
    current["Incidents"] = pd.to_numeric(current["Incidents"], errors="coerce").clip(lower=0).fillna(0)
    current = current[current["Crime_Category"].isin(CATEGORY_TO_FIELD)].copy()
    return current.groupby("Station")["Incidents"].sum()


def _current_features(history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    first_year = int(history["year_start"].min())
    for station, group in history.groupby("station"):
        ordered = group.sort_values("year_start")
        if len(ordered) < 3:
            continue
        latest = ordered.iloc[-3:]
        totals = latest["risk_total"].to_numpy(dtype=float)
        previous = ordered.iloc[-1]
        before = ordered.iloc[-2]
        row: dict[str, object] = {
            "station": station,
            "target_year": "2025/2026",
            "risk_total_lag_1": totals[-1],
            "risk_total_lag_2": totals[-2],
            "risk_total_lag_3": totals[-3],
            "rolling_3_mean": float(totals.mean()),
            "rolling_3_std": float(totals.std(ddof=1)),
            "trend_1_year": float(totals[-1] - totals[-2]),
            "long_term_mean": float(ordered["risk_total"].mean()),
            "year_index": 2025 - first_year,
        }
        for field in RISK_FIELDS:
            row[f"{field}_lag_1"] = float(previous[field])
        rows.append(row)
    return pd.DataFrame(rows)


def train(
    input_path: Path = DEFAULT_INPUT,
    current_path: Path = DEFAULT_CURRENT,
    output_dir: Path = DEFAULT_OUTPUT,
    seed: int = 42,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, correction_count = _prepare_history(input_path)
    target_years = sorted(frame["year_start"].unique())
    if len(target_years) < 5:
        raise ValueError("At least five eligible target years are required")
    test_years = target_years[-3:]
    train_mask = frame["year_start"] < test_years[0]
    test_mask = ~train_mask
    train_frame = frame.loc[train_mask].copy()
    test_frame = frame.loc[test_mask].copy()

    pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                ExtraTreesRegressor(
                    n_estimators=180,
                    min_samples_leaf=8,
                    max_features=0.7,
                    n_jobs=-1,
                    random_state=seed,
                ),
            ),
        ]
    )
    # Learn the change from last year rather than the absolute count. This makes
    # persistence the default and asks the model to learn only a defensible
    # correction. The untouched holdout must beat the plain last-year baseline.
    train_delta = train_frame["risk_total"] - train_frame["risk_total_lag_1"]
    pipeline.fit(train_frame[FEATURES], train_delta)

    def predict_total(feature_frame: pd.DataFrame) -> np.ndarray:
        return np.maximum(
            0,
            feature_frame["risk_total_lag_1"].to_numpy(dtype=float)
            + pipeline.predict(feature_frame[FEATURES]),
        )

    prediction = predict_total(test_frame)
    baseline = test_frame["risk_total_lag_1"].to_numpy(dtype=float)
    test_metrics = _metrics(test_frame["risk_total"], prediction)
    baseline_metrics = _metrics(test_frame["risk_total"], baseline)

    rng = np.random.default_rng(seed)
    base_model_mae = mean_absolute_error(test_frame["risk_total"], prediction)
    importance_rows = []
    for feature in FEATURES:
        increases = []
        for _ in range(5):
            shuffled = test_frame.copy()
            shuffled[feature] = rng.permutation(shuffled[feature].to_numpy())
            increases.append(
                mean_absolute_error(test_frame["risk_total"], predict_total(shuffled))
                - base_model_mae
            )
        importance_rows.append({
            "feature": feature,
            "mae_importance_mean": float(np.mean(increases)),
            "mae_importance_std": float(np.std(increases)),
        })
    importance = pd.DataFrame(
        importance_rows
    ).sort_values("mae_importance_mean", ascending=False)
    importance.to_csv(output_dir / "feature_importance.csv", index=False)

    manifest = frame[["station", "year", "year_start", "risk_total", "risk_total_lag_1"]].copy()
    manifest["split"] = np.where(manifest["year_start"].isin(test_years), "test", "train")
    prediction_lookup = dict(zip(test_frame.index, prediction))
    manifest["model_prediction"] = [prediction_lookup.get(index, np.nan) for index in manifest.index]
    manifest.to_csv(output_dir / "split_manifest.csv", index=False)

    current_features = _current_features(frame)
    current_features["predicted_risk_total"] = predict_total(current_features).round(2)
    observed = _current_observed(current_path)
    current_features["observed_risk_total"] = current_features["station"].map(observed)
    benchmark = current_features.dropna(subset=["observed_risk_total"]).copy()
    current_metrics = _metrics(
        benchmark["observed_risk_total"], benchmark["predicted_risk_total"].to_numpy()
    )
    current_features.to_csv(output_dir / "current_2025_2026_forecast.csv", index=False)

    metrics: dict[str, object] = {
        "model_purpose": "next-year station-level hotspot planning; not person-level prediction",
        "model": "extra-trees regression on year-over-year change, added to last year's count",
        "target": "next-year combined count for five residential/vehicle safety categories",
        "input_sha256": _sha256(input_path),
        "current_benchmark_sha256": _sha256(current_path),
        "source_negative_corrections_clipped_in_feature_layer": correction_count,
        "split_method": "chronological holdout; final three target years untouched",
        "rows_total": int(len(frame)),
        "train_rows": int(len(train_frame)),
        "test_rows": int(len(test_frame)),
        "train_percent": round(100 * len(train_frame) / len(frame), 4),
        "test_percent": round(100 * len(test_frame) / len(frame), 4),
        "train_target_years": [int(year) for year in target_years if year < test_years[0]],
        "test_target_years": [int(year) for year in test_years],
        "test_metrics": test_metrics,
        "last_year_baseline_metrics": baseline_metrics,
        "mae_improvement_over_last_year_percent": round(
            100 * (baseline_metrics["mae"] - test_metrics["mae"]) / baseline_metrics["mae"], 3
        ) if baseline_metrics["mae"] else 0.0,
        "external_2025_2026_benchmark": {
            "stations": int(len(benchmark)),
            **current_metrics,
        },
        "features": FEATURES,
        "seed": seed,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    joblib.dump(
        {
            "delta_model": pipeline,
            "features": FEATURES,
            "prediction_rule": "max(0, risk_total_lag_1 + predicted_change)",
            "trained_at": metrics["generated_at"],
        },
        output_dir / "hotspot_risk_forecast.joblib",
    )

    card = f"""# MzansiMesh hotspot risk forecast model card

## Purpose

This model forecasts a station's next-year combined count across five residential
and vehicle safety categories. It supports patrol planning and never predicts a
person's behaviour, identity, guilt, claim validity or insurance outcome.

## Training design

- Source: SAPS station history from 2008/09 through 2024/25
- Eligible examples: {metrics['rows_total']:,}
- Split: {metrics['train_rows']:,} chronological training rows ({metrics['train_percent']:.1f}%) and
  {metrics['test_rows']:,} untouched recent rows ({metrics['test_percent']:.1f}%)
- Test target years: {', '.join(str(year) + '/' + str(year + 1) for year in test_years)}
- Model: extra-trees regression predicts the year-over-year change; that change is
  added to last year's observed count
- Inputs: lagged station counts, three-year rolling statistics and prior-only trend
- Leakage control: every feature is shifted; no current or future target count is an input
- Source correction handling: {correction_count} negative correction value(s) clipped to zero only in the feature layer

## Untouched historical result

- Model MAE: {test_metrics['mae']:.2f}
- Model RMSE: {test_metrics['rmse']:.2f}
- Model R²: {test_metrics['r2']:.3f}
- Last-year baseline MAE: {baseline_metrics['mae']:.2f}
- MAE improvement over baseline: {metrics['mae_improvement_over_last_year_percent']:.1f}%

## Independent 2025/26 benchmark

The trained model was also compared with the separately supplied 2025/26 quarterly
aggregation across {metrics['external_2025_2026_benchmark']['stations']:,} matched stations.
Its MAE was {current_metrics['mae']:.2f}. The live dashboard uses the actual audited
2025/26 counts, not these predictions; forecasts are retained as benchmark evidence.

## Limitations and governance

- Police-station boundaries do not equal suburb boundaries.
- Recorded incidents reflect reporting and enforcement patterns as well as underlying harm.
- Forecasts require drift checks and human planning review; they must not drive automated
  adverse insurance, policing or individual-level decisions.
"""
    (output_dir / "MODEL_CARD.md").write_text(card, encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    metrics = train(args.input.resolve(), args.current.resolve(), args.output.resolve(), args.seed)
    print("MzansiMesh hotspot forecast training complete")
    print(f"  rows       {metrics['rows_total']:,}")
    print(f"  split      {metrics['train_percent']:.1f}% train / {metrics['test_percent']:.1f}% test")
    print(f"  test MAE   {metrics['test_metrics']['mae']:.2f}")
    print(f"  baseline   {metrics['last_year_baseline_metrics']['mae']:.2f}")
    print(f"  outputs    {args.output.resolve()}")


if __name__ == "__main__":
    main()
