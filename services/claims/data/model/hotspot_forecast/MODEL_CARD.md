# MzansiMesh hotspot risk forecast model card

## Purpose

This model forecasts a station's next-year combined count across five residential
and vehicle safety categories. It supports patrol planning and never predicts a
person's behaviour, identity, guilt, claim validity or insurance outcome.

## Training design

- Source: SAPS station history from 2008/09 through 2024/25
- Eligible examples: 16,060
- Split: 12,661 chronological training rows (78.8%) and
  3,399 untouched recent rows (21.2%)
- Test target years: 2022/2023, 2023/2024, 2024/2025
- Model: extra-trees regression predicts the year-over-year change; that change is
  added to last year's observed count
- Inputs: lagged station counts, three-year rolling statistics and prior-only trend
- Leakage control: every feature is shifted; no current or future target count is an input
- Source correction handling: 1 negative correction value(s) clipped to zero only in the feature layer

## Untouched historical result

- Model MAE: 36.95
- Model RMSE: 69.19
- Model R²: 0.962
- Last-year baseline MAE: 38.41
- MAE improvement over baseline: 3.8%

## Independent 2025/26 benchmark

The trained model was also compared with the separately supplied 2025/26 quarterly
aggregation across 1,124 matched stations.
Its MAE was 32.16. The live dashboard uses the actual audited
2025/26 counts, not these predictions; forecasts are retained as benchmark evidence.

## Limitations and governance

- Police-station boundaries do not equal suburb boundaries.
- Recorded incidents reflect reporting and enforcement patterns as well as underlying harm.
- Forecasts require drift checks and human planning review; they must not drive automated
  adverse insurance, policing or individual-level decisions.
