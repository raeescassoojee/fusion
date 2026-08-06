from __future__ import annotations

import importlib.util
from pathlib import Path


def _training_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "services" / "claims" / "src" / "train_hotspot_risk_forecast.py"
    spec = importlib.util.spec_from_file_location("hotspot_forecast_training", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hotspot_forecast_uses_chronological_holdout_and_beats_baseline(tmp_path):
    module = _training_module()
    metrics = module.train(
        module.DEFAULT_INPUT,
        module.DEFAULT_CURRENT,
        tmp_path,
        seed=42,
    )

    assert metrics["rows_total"] == metrics["train_rows"] + metrics["test_rows"]
    assert 78 <= metrics["train_percent"] <= 80
    assert 20 <= metrics["test_percent"] <= 22
    assert metrics["test_target_years"] == [2022, 2023, 2024]
    assert metrics["test_metrics"]["mae"] < metrics["last_year_baseline_metrics"]["mae"]
    assert metrics["external_2025_2026_benchmark"]["stations"] > 1_000
    assert (tmp_path / "hotspot_risk_forecast.joblib").is_file()
    assert (tmp_path / "metrics.json").is_file()
    assert (tmp_path / "split_manifest.csv").is_file()
    assert (tmp_path / "feature_importance.csv").is_file()
    assert (tmp_path / "current_2025_2026_forecast.csv").is_file()
    assert (tmp_path / "MODEL_CARD.md").is_file()
