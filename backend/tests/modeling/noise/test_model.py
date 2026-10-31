"""Artifact contract tests: arithmetic, imputation, confidence, validation."""
from __future__ import annotations

import json
import math

import pytest

from app.contracts.constants import TimeSlot
from app.modeling.noise import model

FEATURES = ["a_pred", "poi_pub_100m", "nearest_industrial_m"]


def _artifact(**overrides) -> dict:
    slots = {}
    for slot in TimeSlot:
        slots[slot.value] = {
            "intercept": 0.5,
            "weights": {"a_pred": 1.0, "poi_pub_100m": 0.2, "nearest_industrial_m": -0.001},
            "impute": {"a_pred": 55.0, "poi_pub_100m": 3.0, "nearest_industrial_m": 2000.0},
            "cv": {"mae": 6.0, "rmse": 7.0},
        }
    artifact = {
        "schema_version": model.ARTIFACT_SCHEMA_VERSION,
        "model_id": "test_model",
        "model_version": "0.0.1",
        "created_at": "2026-11-01T00:00:00+09:00",
        "status": "estimated",
        "method": "model",
        "source": "sdot",
        "training": {},
        "features": [{"name": name, "kind": "numeric", "description": name} for name in FEATURES],
        "baseline": model.DEFAULT_BASELINE,
        "confidence": model.DEFAULT_CONFIDENCE,
        "slots": slots,
        "metrics": {},
        "adoption": {},
    }
    artifact.update(overrides)
    return artifact


def test_predict_is_baseline_plus_linear_residual():
    loaded = model.NoiseModel(_artifact())
    features = {"a_pred": 60.0, "poi_pub_100m": 5.0, "nearest_industrial_m": 100.0}
    residual = 0.5 + 1.0 * 60.0 + 0.2 * 5.0 - 0.001 * 100.0
    assert loaded.residual("night", features) == pytest.approx(residual)
    assert loaded.predict("night", features) == pytest.approx(60.0 + residual)


def test_missing_features_use_training_medians():
    loaded = model.NoiseModel(_artifact())
    clean = loaded.predict("day", {"a_pred": 60.0, "poi_pub_100m": 3.0, "nearest_industrial_m": 2000.0})
    imputed = loaded.predict("day", {"a_pred": 60.0, "poi_pub_100m": math.nan, "nearest_industrial_m": None})
    assert imputed == pytest.approx(clean)


def test_missing_baseline_uses_baseline_median():
    loaded = model.NoiseModel(_artifact())
    value = loaded.predict("day", {"a_pred": math.nan, "poi_pub_100m": 3.0, "nearest_industrial_m": 2000.0})
    assert value == pytest.approx(55.0 + 0.5 + 1.0 * 55.0 + 0.2 * 3.0 - 0.001 * 2000.0)


def test_confidence_is_a_clipped_cv_heuristic():
    loaded = model.NoiseModel(_artifact())
    assert loaded.confidence("night") == pytest.approx(0.5)  # 1 - 6/12
    assert loaded.confidence("night", fallback_used=True) == pytest.approx(0.3)  # x 0.6


def test_confidence_clips_at_bounds():
    artifact = _artifact()
    for config in artifact["slots"].values():
        config["cv"]["mae"] = 0.0
    assert model.NoiseModel(artifact).confidence("night") == pytest.approx(0.9)
    for config in artifact["slots"].values():
        config["cv"]["mae"] = 100.0
    assert model.NoiseModel(artifact).confidence("night") == pytest.approx(0.2)


def test_missing_cv_mae_uses_min_confidence():
    artifact = _artifact()
    for config in artifact["slots"].values():
        config["cv"] = {}
    loaded = model.NoiseModel(artifact)
    assert loaded.confidence("night") == pytest.approx(0.2)
    assert loaded.slot_mae("night") is None


def test_write_load_round_trip_is_deterministic(tmp_path):
    path = model.write_artifact(tmp_path / "artifact.json", _artifact())
    first = model.NoiseModel.load(path)
    second = model.NoiseModel.load(path)
    features = {"a_pred": 58.0, "poi_pub_100m": 4.0, "nearest_industrial_m": 250.0}
    assert first.predict("evening", features) == second.predict("evening", features)
    assert first.method == "model" and first.status == "estimated"
    assert json.loads(path.read_text(encoding="utf-8"))["model_id"] == "test_model"


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": 99},
        {"model_id": ""},
        {"slots": {slot.value: {"intercept": 0.0, "weights": {}, "impute": {}, "cv": {}} for slot in TimeSlot}},
        {"features": [{"name": "a_pred"}, {"name": "a_pred"}, {"name": "nearest_industrial_m"}]},
    ],
)
def test_inconsistent_artifacts_are_rejected(override):
    with pytest.raises(model.ArtifactError):
        model.NoiseModel(_artifact(**override))


def test_non_finite_coefficient_rejected():
    artifact = _artifact()
    artifact["slots"]["night"]["intercept"] = math.nan
    with pytest.raises(model.ArtifactError):
        model.NoiseModel(artifact)


def test_missing_artifact_file_and_bad_json(tmp_path):
    with pytest.raises(model.ArtifactError):
        model.NoiseModel.load(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(model.ArtifactError):
        model.NoiseModel.load(broken)


def test_write_artifact_validates_before_writing(tmp_path):
    path = tmp_path / "artifact.json"
    with pytest.raises(model.ArtifactError):
        model.write_artifact(path, _artifact(schema_version=99))
    assert not path.exists()


def test_slot_enum_and_string_are_equivalent():
    loaded = model.NoiseModel(_artifact())
    features = {"a_pred": 50.0, "poi_pub_100m": 1.0, "nearest_industrial_m": 10.0}
    assert loaded.predict(TimeSlot.night, features) == loaded.predict("night", features)
    with pytest.raises(ValueError):
        loaded.predict("midnight", features)
