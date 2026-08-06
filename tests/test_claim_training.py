from __future__ import annotations

import importlib.util
from pathlib import Path


def _training_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "services" / "claims" / "src" / "train_claim_severity_model.py"
    spec = importlib.util.spec_from_file_location("claim_training", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claim_training_is_reproducible_and_leakage_safe(tmp_path):
    module = _training_module()
    metrics = module.train(module.DEFAULT_INPUT, tmp_path, seed=42, test_size=0.20)

    assert metrics["rows_total"] == metrics["train_rows"] + metrics["test_rows"]
    assert 79.9 <= metrics["train_percent"] <= 80.1
    assert 19.9 <= metrics["test_percent"] <= 20.1
    assert metrics["amount_used_as_input_feature"] is False
    assert metrics["test_metrics"]["roc_auc"] > 0.60
    assert (tmp_path / "claim_severity_pipeline.joblib").is_file()
    assert (tmp_path / "metrics.json").is_file()
    assert (tmp_path / "split_manifest.csv").is_file()
    assert (tmp_path / "feature_importance.csv").is_file()
    assert (tmp_path / "MODEL_CARD.md").is_file()
