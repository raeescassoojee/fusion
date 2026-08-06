"""Train the Sentinel Mesh claims-severity triage model.

This is deliberately NOT a fraud classifier: the supplied claims workbook has
no confirmed fraud/non-fraud outcome.  The supervised target is whether the
claim amount is at least the configured high-value threshold.  CLAIM_AMOUNT is
used only to construct that label and is excluded from the model inputs.

The script produces a reproducible stratified 80/20 split, performs five-fold
cross-validation on the 80% training partition, evaluates once on the untouched
20% test partition, and writes the fitted pipeline plus auditable artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


CLAIMS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = CLAIMS_ROOT / "data" / "raw" / "claims_workbook.xlsx"
DEFAULT_OUTPUT = CLAIMS_ROOT / "data" / "model"

CATEGORICAL_FEATURES = [
    "PERIL",
    "SUBURB",
    "ITEM_TYPE",
    "VEHICLE_MAKE",
    "ITEM_CATEGORY",
    "ITEM_PERIL_DESCR",
    "INCIDENT_DAY_NAME",
]
NUMERIC_FEATURES = [
    "VEHICLE_YEAR",
    "INCIDENT_HOUR",
    "INCIDENT_MONTH",
    "VEHICLE_AGE_AT_INCIDENT",
]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _prepare_claims(path: Path, threshold: float) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    frame = pd.read_excel(path)
    frame.columns = [str(column).strip().upper() for column in frame.columns]
    required = {"INCIDENT", "CLAIM_AMOUNT", "INCIDENT_DATE_TIME", *CATEGORICAL_FEATURES[:-1]}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"claims workbook is missing columns: {', '.join(missing)}")

    frame["CLAIM_AMOUNT"] = pd.to_numeric(frame["CLAIM_AMOUNT"], errors="coerce")
    frame["INCIDENT_DATE_TIME"] = pd.to_datetime(
        frame["INCIDENT_DATE_TIME"], errors="coerce"
    )
    frame = frame.loc[frame["CLAIM_AMOUNT"].notna() & (frame["CLAIM_AMOUNT"] > 0)].copy()

    incident_time = frame["INCIDENT_DATE_TIME"]
    frame["INCIDENT_HOUR"] = incident_time.dt.hour
    frame["INCIDENT_MONTH"] = incident_time.dt.month
    frame["INCIDENT_DAY_NAME"] = incident_time.dt.day_name()
    vehicle_year = pd.to_numeric(frame.get("VEHICLE_YEAR"), errors="coerce")
    frame["VEHICLE_YEAR"] = vehicle_year
    frame["VEHICLE_AGE_AT_INCIDENT"] = (
        incident_time.dt.year - vehicle_year
    ).where(vehicle_year.notna()).clip(lower=0, upper=80)

    for column in CATEGORICAL_FEATURES:
        frame[column] = frame[column].fillna("UNKNOWN").astype(str).str.strip()
        frame.loc[frame[column].eq(""), column] = "UNKNOWN"

    target = (frame["CLAIM_AMOUNT"] >= threshold).astype(np.int8)
    incident_ids = frame["INCIDENT"].fillna("UNKNOWN").astype(str)
    return frame[FEATURES].copy(), target, incident_ids


def _pipeline(seed: int) -> Pipeline:
    categorical = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "one_hot",
                OneHotEncoder(
                    handle_unknown="ignore",
                    min_frequency=10,
                    sparse_output=True,
                ),
            ),
        ]
    )
    numeric = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    preprocessor = ColumnTransformer(
        transformers=[
            ("categorical", categorical, CATEGORICAL_FEATURES),
            ("numeric", numeric, NUMERIC_FEATURES),
        ]
    )
    classifier = LogisticRegression(
        max_iter=1500,
        class_weight="balanced",
        solver="liblinear",
        random_state=seed,
    )
    return Pipeline(
        steps=[("preprocessor", preprocessor), ("classifier", classifier)]
    )


def _float(value: object) -> float:
    return round(float(value), 6)


def train(
    input_path: Path,
    output_dir: Path,
    threshold: float = 75_000.0,
    test_size: float = 0.20,
    seed: int = 42,
) -> dict[str, object]:
    if not 0.05 <= test_size <= 0.50:
        raise ValueError("test_size must be between 0.05 and 0.50")
    output_dir.mkdir(parents=True, exist_ok=True)
    features, target, incident_ids = _prepare_claims(input_path, threshold)

    indices = np.arange(len(features))
    train_indices, test_indices = train_test_split(
        indices,
        test_size=test_size,
        random_state=seed,
        shuffle=True,
        stratify=target,
    )
    x_train = features.iloc[train_indices]
    x_test = features.iloc[test_indices]
    y_train = target.iloc[train_indices]
    y_test = target.iloc[test_indices]

    model = _pipeline(seed)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    validation = cross_validate(
        model,
        x_train,
        y_train,
        cv=folds,
        scoring={
            "roc_auc": "roc_auc",
            "f1": "f1",
            "balanced_accuracy": "balanced_accuracy",
        },
        n_jobs=-1,
        return_train_score=False,
    )
    model.fit(x_train, y_train)
    prediction = model.predict(x_test)
    probability = model.predict_proba(x_test)[:, 1]
    matrix = confusion_matrix(y_test, prediction, labels=[0, 1])

    metrics: dict[str, object] = {
        "model_purpose": "high-value claim triage; not fraud prediction",
        "target": f"CLAIM_AMOUNT >= R{threshold:,.0f}",
        "amount_used_as_input_feature": False,
        "input_sha256": _sha256(input_path),
        "rows_total": int(len(features)),
        "train_rows": int(len(train_indices)),
        "test_rows": int(len(test_indices)),
        "train_percent": _float(100 * len(train_indices) / len(features)),
        "test_percent": _float(100 * len(test_indices) / len(features)),
        "positive_rate_total": _float(target.mean()),
        "seed": seed,
        "test_metrics": {
            "accuracy": _float(accuracy_score(y_test, prediction)),
            "balanced_accuracy": _float(balanced_accuracy_score(y_test, prediction)),
            "precision": _float(precision_score(y_test, prediction, zero_division=0)),
            "recall": _float(recall_score(y_test, prediction, zero_division=0)),
            "f1": _float(f1_score(y_test, prediction, zero_division=0)),
            "roc_auc": _float(roc_auc_score(y_test, probability)),
            "confusion_matrix": {
                "true_negative": int(matrix[0, 0]),
                "false_positive": int(matrix[0, 1]),
                "false_negative": int(matrix[1, 0]),
                "true_positive": int(matrix[1, 1]),
            },
        },
        "training_cross_validation": {
            metric.removeprefix("test_"): {
                "mean": _float(values.mean()),
                "standard_deviation": _float(values.std()),
                "fold_values": [_float(value) for value in values],
            }
            for metric, values in validation.items()
            if metric.startswith("test_")
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    model_path = output_dir / "claim_severity_pipeline.joblib"
    joblib.dump(model, model_path)

    names = model.named_steps["preprocessor"].get_feature_names_out()
    coefficients = model.named_steps["classifier"].coef_[0]
    importance = pd.DataFrame(
        {
            "feature": names,
            "coefficient": coefficients,
            "absolute_importance": np.abs(coefficients),
            "effect": np.where(coefficients >= 0, "increases high-value likelihood", "decreases high-value likelihood"),
        }
    ).sort_values("absolute_importance", ascending=False)
    importance.to_csv(output_dir / "feature_importance.csv", index=False)

    manifest = pd.DataFrame(
        {
            "row_index": indices,
            "incident_id": incident_ids.to_numpy(),
            "split": np.where(np.isin(indices, test_indices), "test", "train"),
            "high_value_target": target.to_numpy(),
        }
    ).sort_values("row_index")
    manifest.to_csv(output_dir / "split_manifest.csv", index=False)

    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    model_card = f"""# Sentinel Mesh claims severity model card

## Purpose

This model supports **high-value claims triage**. It does not predict fraud and
must not be used to automatically reject, delay or approve a claim.

## Training design

- Source rows: {metrics['rows_total']:,}
- Target: `{metrics['target']}`
- Input leakage control: `CLAIM_AMOUNT` is not an input feature
- Split: {metrics['train_rows']:,} training rows ({metrics['train_percent']:.1f}%) and
  {metrics['test_rows']:,} untouched test rows ({metrics['test_percent']:.1f}%)
- Split method: stratified random split with seed `{seed}`
- Training validation: five-fold stratified cross-validation on the training set
- Model: class-balanced logistic regression with one-hot categorical features

## Untouched test result

- ROC-AUC: {metrics['test_metrics']['roc_auc']:.3f}
- F1: {metrics['test_metrics']['f1']:.3f}
- Balanced accuracy: {metrics['test_metrics']['balanced_accuracy']:.3f}
- Precision: {metrics['test_metrics']['precision']:.3f}
- Recall: {metrics['test_metrics']['recall']:.3f}

## Limitations and governance

- The workbook contains no confirmed fraud/non-fraud label. Calling this a fraud
  model would be technically incorrect.
- The target is an operational severity threshold, not a measure of member intent.
- Suburb and vehicle fields can encode socioeconomic or geographic bias. Results
  require monitoring, human review and fairness testing before a pilot.
- This prototype must never make an automated adverse claims decision.
"""
    (output_dir / "MODEL_CARD.md").write_text(model_card, encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=75_000.0)
    parser.add_argument("--test-size", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    metrics = train(
        args.input.resolve(),
        args.output.resolve(),
        args.threshold,
        args.test_size,
        args.seed,
    )
    test = metrics["test_metrics"]
    print("Sentinel Mesh claims severity training complete")
    print(f"  rows       {metrics['rows_total']:,}")
    print(f"  split      {metrics['train_percent']:.1f}% train / {metrics['test_percent']:.1f}% test")
    print(f"  ROC-AUC    {test['roc_auc']:.3f}")
    print(f"  F1         {test['f1']:.3f}")
    print(f"  outputs    {args.output.resolve()}")


if __name__ == "__main__":
    main()
