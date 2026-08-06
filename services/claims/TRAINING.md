# MzansiMesh model training and evidence

The backend now contains two reproducible training problems. They are kept
separate because they answer different questions and require different split
strategies. Neither model makes an automatic adverse decision.

## Model 1 — claims severity triage

### What it predicts

The Discovery workbook has no confirmed fraud/non-fraud outcome. This model
therefore predicts whether a claim is high value (`CLAIM_AMOUNT >= R75,000`) for
investigator queue triage. It is not a fraud classifier.

### Where it is implemented

```text
services/claims/src/train_claim_severity_model.py
```

Run it from the repository root:

```powershell
python services\claims\src\train_claim_severity_model.py
```

### 80/20 design

1. Read `services/claims/data/raw/claims_workbook.xlsx`.
2. Keep valid positive claim amounts.
3. Build the high-value target, then remove claim amount from model inputs.
4. Create a stratified random 80% training / 20% untouched test split (seed 42).
5. Run five-fold cross-validation only inside the training partition.
6. Fit the final class-balanced logistic regression on the full training partition.
7. Evaluate once on the test partition.

Inputs are peril, suburb, item/vehicle attributes and incident-time features.
Incident IDs and claim amount are never inputs.

### Latest result

- 15,631 eligible rows
- 12,504 training / 3,127 test rows
- Holdout ROC-AUC: 0.730
- Holdout F1: 0.522
- Holdout balanced accuracy: 0.681

Artifacts are in `services/claims/data/model/`:

- `claim_severity_pipeline.joblib`
- `metrics.json`
- `split_manifest.csv`
- `feature_importance.csv`
- `MODEL_CARD.md`

## Model 2 — next-year hotspot risk forecast

### What it predicts

This model forecasts the next financial year's combined station count for:

- burglary at residential premises;
- robbery at residential premises;
- theft of motor vehicle and motorcycle;
- carjacking; and
- theft out of or from a motor vehicle.

It supports hotspot and patrol planning. It is not a prediction about a person,
claimant, household or future offence.

### Where it is implemented

```text
services/claims/src/train_hotspot_risk_forecast.py
```

Run it from the repository root:

```powershell
python services\claims\src\train_hotspot_risk_forecast.py
```

### Chronological 80/20 design

A random 80/20 split is unsafe for forecasting because future years can leak into
training. The script instead creates lagged examples and holds out the final three
target years:

1. Read 2008/09–2024/25 station history.
2. Clip negative official correction values to zero only in the feature layer;
   immutable source files remain unchanged.
3. Build features from the previous one to three years only.
4. Train on 2011/12–2021/22 target years (78.8%).
5. Test on untouched 2022/23–2024/25 target years (21.2%).
6. Predict the year-over-year change with an Extra Trees regressor and add it to
   the last observed count.
7. Compare against a strong “same as last year” baseline.
8. Independently compare the forecast with the separately supplied 2025/26 four-
   quarter aggregation. The live dashboard uses actual 2025/26 counts, not the
   model forecast.

### Latest result

- 16,060 eligible station/year examples
- 12,661 training / 3,399 untouched test rows
- Test MAE: 36.95 incidents
- Test RMSE: 69.19 incidents
- Test R²: 0.962
- Last-year baseline MAE: 38.41 incidents
- MAE improvement over baseline: 3.81%
- Independent 2025/26 benchmark: 1,124 matched stations, MAE 32.16

Artifacts are in `services/claims/data/model/hotspot_forecast/`:

- `hotspot_risk_forecast.joblib`
- `metrics.json`
- `split_manifest.csv`
- `feature_importance.csv`
- `current_2025_2026_forecast.csv`
- `MODEL_CARD.md`

## Data integration and reruns

Prepared, audited files are in `services/claims/data/partner/` and runtime outputs
are in `services/claims/data/curated/`. Refresh current scores without copying the
large raw workbooks:

```powershell
python services\claims\src\run_enhancements.py
```

To rebuild the prepared CSVs from the teammate archive, first copy its `annual`
and `quarterly` directories beneath `services/claims/data/partner/`, then run:

```powershell
python services\claims\src\run_enhancements.py --rebuild-source
```

## What not to train from scratch before the final

- Do not train a face-recognition network from scratch. Keep YuNet + SFace and
  calibrate thresholds on consented demo identities at close, medium and far range.
- Do not train OCR from scratch. Benchmark detection, per-frame reads and multi-frame
  voting on controlled plate fixtures.
- Do not call claims severity “fraud prediction” until a governed, outcome-labelled
  dataset exists.
- Do not feed station forecasts into person-level decisions.

## Production gates

Before a pilot, add geographic/fairness analysis, drift monitoring, retained-data
deletion enforcement, independent threshold validation and documented human review.
