"""End-to-end (small, offline) cross-validation and adoption-decision tests."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

from app.modeling.noise import cv, experiment
from app.modeling.noise import features as spatial

_TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)


def _synthetic_frame() -> pd.DataFrame:
    rows = []
    slot_offset = {"day": 6.0, "evening": 5.0, "night": 3.0, "dawn": 1.0}
    for i in range(8):
        for j in range(8):
            x, y = 950_000 + i * 300, 1_950_000 + j * 300
            lng, lat = _TO_WGS.transform(x, y)
            for k, (slot, offset) in enumerate(slot_offset.items()):
                rows.append(
                    {
                        "sensor_id": f"S{i}{j}",
                        "lng": lng,
                        "lat": lat,
                        "slot": slot,
                        "noise_db": 45.0 + offset + 3.0 * math.sin(i + j + k),
                    }
                )
    frame = pd.DataFrame(rows)
    frame["block"] = cv.block_ids(frame["lng"], frame["lat"])
    frame["roadside"] = False
    for name in spatial.feature_names():
        if "nearest" in name:
            frame[name] = spatial.DEFAULT_CAP_M
        else:
            frame[name] = 0.0
    frame.loc[frame.index % 7 == 0, "nearest_major_road_m"] = 20.0
    frame["roadside"] = frame["nearest_major_road_m"] <= experiment.ROADSIDE_MAX_M
    return frame


def test_run_cv_produces_disjoint_fold_metrics():
    frame = _synthetic_frame()
    fold_table, predictions = experiment.run_cv(frame, n_splits=2, inner_splits=2, seed=0)

    assert set(fold_table["model"]) == set(experiment.MODELS)
    assert set(fold_table["fold"]) == {0, 1}
    assert set(fold_table["subset"]) == {"all", "roadside", "residential"}
    assert fold_table["n"].gt(0).all()
    assert np.isfinite(fold_table["mae"]).all()
    # Every test row is a real sensor-slot row and A has a neighbour everywhere here.
    assert predictions[experiment.MODEL_A].notna().all()


def test_fold_improvements_and_adoption():
    def table(b_mae_by_fold):
        rows = []
        for fold in range(5):
            rows.append({"model": "A_idw", "subset": "all", "fold": fold, "mae": 2.0})
            rows.append({"model": "B_gb", "subset": "all", "fold": fold, "mae": b_mae_by_fold[fold]})
        return pd.DataFrame(rows)

    good = table([1.5, 1.5, 1.5, 1.5, 2.2])
    improvement = experiment.fold_improvements(good, "B_gb")
    assert improvement["folds_improved"] == 4
    assert improvement["mean_gain_db"] == pytest.approx((0.5 * 4 - 0.2) / 5)

    external = pd.DataFrame(
        [
            {"model": "A_idw", "spearman": 0.5},
            {"model": "B_gb", "spearman": 0.6},
        ]
    )
    verdict = experiment.decide(good, external)
    assert verdict["models"]["B_gb"]["adopted"] is True
    assert verdict["recommendation"] == "B 채택 후보"

    weak = table([1.9, 1.9, 1.9, 1.9, 2.1])
    assert experiment.decide(weak, external)["recommendation"] == "A 유지"


def test_adoption_requires_external_rank_not_worse():
    rows = [
        {"model": "A_idw", "subset": "all", "fold": f, "mae": 2.0} for f in range(5)
    ] + [{"model": "B_gb", "subset": "all", "fold": f, "mae": 1.5} for f in range(5)]
    external = pd.DataFrame(
        [
            {"model": "A_idw", "spearman": 0.8},
            {"model": "B_gb", "spearman": 0.4},
        ]
    )
    verdict = experiment.decide(pd.DataFrame(rows), external)
    assert verdict["models"]["B_gb"]["adopted"] is False
    assert verdict["recommendation"] == "A 유지"
