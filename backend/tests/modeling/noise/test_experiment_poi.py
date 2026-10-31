"""Experiment tests: weight conversion, monotonicity, adoption decision, artifact metadata."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.contracts.constants import TimeSlot
from app.modeling.noise import experiment_poi as exp


def test_linear_to_raw_matches_pipeline_prediction():
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "a_pred": rng.normal(55, 5, 200),
            "poi_pub_100m": rng.poisson(2, 200).astype(float),
            "nearest_pub_m": rng.uniform(0, 500, 200),
        }
    )
    frame.loc[::17, "poi_pub_100m"] = np.nan
    target = 0.3 * frame["poi_pub_100m"].fillna(2) - 0.002 * frame["nearest_pub_m"] + 0.1
    pipeline = exp._linear_pipeline()
    pipeline.fit(frame.to_numpy(dtype=float), target.to_numpy(dtype=float))

    features = list(frame.columns)
    raw = exp.linear_to_raw(pipeline, features)
    for row in frame.itertuples(index=False):
        values = {
            name: (raw["impute"][name] if not math.isfinite(getattr(row, name)) else float(getattr(row, name)))
            for name in features
        }
        manual = raw["intercept"] + sum(raw["weights"][name] * value for name, value in values.items())
        expected = float(pipeline.predict(np.array([[getattr(row, name) for name in features]]))[0])
        assert manual == pytest.approx(expected, abs=1e-8)


def test_monotonic_constraints_encode_physical_expectation():
    signs = dict(zip(exp.POI_MODEL_FEATURES, exp.monotonic_constraints(exp.POI_MODEL_FEATURES)))
    assert signs["a_pred"] == 1
    assert signs["poi_pub_100m"] == 1
    assert signs["poi_all_300m"] == 1
    assert signs["industrial_area_ratio_300m"] == 1
    assert signs["major_road_len_100m"] == 1
    assert signs["rail_ground_len_300m"] == 1
    assert signs["nearest_pub_m"] == -1
    assert signs["nearest_industrial_m"] == -1
    assert signs["nearest_major_road_m"] == -1


def test_feature_descriptions_cover_the_model_features_in_order():
    entries = exp.feature_descriptions()
    assert [entry["name"] for entry in entries] == exp.POI_MODEL_FEATURES
    assert len({entry["name"] for entry in entries}) == len(entries)
    assert all(entry["kind"] and entry["description"] for entry in entries)


def test_predict_per_slot_injects_baseline_column():
    frame = pd.DataFrame({"slot": ["day", "night", "day"], "poi_pub_100m": [1.0, 2.0, 3.0]})
    a_pred = np.array([50.0, 51.0, 52.0])

    class Echo:
        def predict(self, matrix):
            assert "a_pred" not in frame.columns  # never read from the frame
            return matrix[:, 0]

    result = exp._predict_per_slot({"day": Echo(), "night": Echo()}, frame, ["a_pred", "poi_pub_100m"], a_pred)
    assert result.tolist() == [50.0, 51.0, 52.0]


def _fold_table(mae_by_model: dict[str, list[float]]) -> pd.DataFrame:
    rows = []
    for model_name, maes in mae_by_model.items():
        for fold, mae in enumerate(maes):
            rows.append({
                "model": model_name, "fold": fold, "subset": "all", "slot": "all",
                "n": 100, "mae": mae, "rmse": 7.0, "spearman": 0.3,
            })
    return pd.DataFrame(rows)


def test_fold_improvements_counts_folds_and_mean_gain():
    table = _fold_table({
        exp.MODEL_B: [6.0, 6.0, 6.0, 6.0, 6.0],
        exp.MODEL_POI_RIDGE: [5.6, 5.9, 6.1, 5.7, 5.8],  # gains 0.4, 0.1, -0.1, 0.3, 0.2
    })
    result = exp.fold_improvements(table, exp.MODEL_POI_RIDGE)
    assert result["folds"] == 5 and result["folds_improved"] == 4
    assert result["mean_gain_db"] == pytest.approx((0.4 + 0.1 - 0.1 + 0.3 + 0.2) / 5)


def _external(rho_ridge: float, rho_gb: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"model": exp.MODEL_B, "spearman": 0.214},
            {"model": exp.MODEL_POI_RIDGE, "spearman": rho_ridge},
            {"model": exp.MODEL_POI_GB, "spearman": rho_gb},
        ]
    )


def test_decide_requires_four_folds_gain_and_no_external_regression():
    table = _fold_table({
        exp.MODEL_B: [6.0] * 5,
        exp.MODEL_POI_RIDGE: [5.6, 5.6, 5.6, 5.6, 6.0],  # 4/5 folds, mean gain 0.32
        exp.MODEL_POI_GB: [5.6] * 5,                      # 5/5, mean gain 0.4
    })
    adopted = exp.decide(table, _external(rho_ridge=0.30, rho_gb=0.30))
    assert adopted["adopted"] == [exp.MODEL_POI_RIDGE, exp.MODEL_POI_GB]
    assert adopted["models"][exp.MODEL_POI_RIDGE]["external_ok"] is True
    assert adopted["recommendation"].startswith(exp.MODEL_POI_RIDGE)


def test_decide_rejects_when_external_rank_worsens():
    table = _fold_table({
        exp.MODEL_B: [6.0] * 5,
        exp.MODEL_POI_RIDGE: [5.0] * 5,
        exp.MODEL_POI_GB: [6.5] * 5,
    })
    verdict = exp.decide(table, _external(rho_ridge=0.05, rho_gb=-0.3))
    assert verdict["adopted"] == []
    assert verdict["recommendation"] == "B 유지"
    assert verdict["models"][exp.MODEL_POI_RIDGE]["external_ok"] is False


def test_decide_rejects_insufficient_mean_gain():
    table = _fold_table({
        exp.MODEL_B: [6.0] * 5,
        exp.MODEL_POI_RIDGE: [5.95, 5.95, 5.95, 5.95, 5.95],  # 5 folds, only 0.05 dB
        exp.MODEL_POI_GB: [5.9] * 5,
    })
    verdict = exp.decide(table, _external(rho_ridge=0.5, rho_gb=0.5))
    assert verdict["adopted"] == []


def test_decide_without_external_never_adopts():
    table = _fold_table({
        exp.MODEL_B: [6.0] * 5,
        exp.MODEL_POI_RIDGE: [5.0] * 5,
        exp.MODEL_POI_GB: [5.0] * 5,
    })
    verdict = exp.decide(table, pd.DataFrame())
    assert verdict["adopted"] == []
    assert math.isnan(verdict["models"][exp.MODEL_POI_RIDGE]["external_spearman"])


def test_coefficient_table_lists_every_feature_per_slot():
    slot_models = {
        slot.value: {
            "intercept": 0.1,
            "weights": {name: 0.01 for name in exp.POI_MODEL_FEATURES},
            "impute": {name: 1.0 for name in exp.POI_MODEL_FEATURES},
        }
        for slot in TimeSlot
    }
    table = exp.coefficient_table(slot_models)
    assert len(table) == len(TimeSlot) * len(exp.POI_MODEL_FEATURES)
    assert set(table["slot"]) == {slot.value for slot in TimeSlot}
    assert set(table["feature"]) == set(exp.POI_MODEL_FEATURES)


def test_artifact_records_adoption_and_hyperparameters():
    frame = pd.DataFrame({"sensor_id": ["S1"] * 4, "slot": list(TimeSlot)})
    slots = {
        slot.value: {
            "intercept": 0.0,
            "weights": {name: 0.0 for name in exp.POI_MODEL_FEATURES},
            "impute": {name: 1.0 for name in exp.POI_MODEL_FEATURES},
            "cv": {"mae": 6.0},
        }
        for slot in TimeSlot
    }
    fold_table = _fold_table({
        exp.MODEL_B: [6.0] * 5,
        exp.MODEL_POI_RIDGE: [5.5] * 5,
    })
    artifact = exp.build_model_artifact(
        frame=frame,
        slot_models=slots,
        fold_table=fold_table,
        created_at="2026-11-01T00:00:00+09:00",
        source_notes={"stores": "test"},
        adoption={"status": "not_adopted", "recommendation": "B 유지"},
    )
    assert artifact["adoption"]["status"] == "not_adopted"
    assert artifact["training"]["hyperparameters"]["model"] == "Ridge"
    assert artifact["baseline"]["fallback"]["max_distance_m"] == 5000.0
    assert [entry["name"] for entry in artifact["features"]] == exp.POI_MODEL_FEATURES
