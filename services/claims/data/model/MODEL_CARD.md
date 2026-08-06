# Sentinel Mesh claims severity model card

## Purpose

This model supports **high-value claims triage**. It does not predict fraud and
must not be used to automatically reject, delay or approve a claim.

## Training design

- Source rows: 15,631
- Target: `CLAIM_AMOUNT >= R75,000`
- Input leakage control: `CLAIM_AMOUNT` is not an input feature
- Split: 12,504 training rows (80.0%) and
  3,127 untouched test rows (20.0%)
- Split method: stratified random split with seed `42`
- Training validation: five-fold stratified cross-validation on the training set
- Model: class-balanced logistic regression with one-hot categorical features

## Untouched test result

- ROC-AUC: 0.730
- F1: 0.522
- Balanced accuracy: 0.681
- Precision: 0.471
- Recall: 0.585

## Limitations and governance

- The workbook contains no confirmed fraud/non-fraud label. Calling this a fraud
  model would be technically incorrect.
- The target is an operational severity threshold, not a measure of member intent.
- Suburb and vehicle fields can encode socioeconomic or geographic bias. Results
  require monitoring, human review and fairness testing before a pilot.
- This prototype must never make an automated adverse claims decision.
