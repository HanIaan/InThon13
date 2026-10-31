"""Spatial block split and metric tests."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.modeling.noise import cv


def test_block_ids_are_1km_metric_blocks():
    # Same 1 km block: points a few metres apart.
    ids = cv.block_ids([126.978, 126.9781], [37.5665, 37.5666])
    assert ids[0] == ids[1]
    # ~3 km east -> different block.
    far = cv.block_ids([126.978, 127.013], [37.5665, 37.5665])
    assert far[0] != far[1]


def test_folds_never_split_a_block():
    groups = np.array([f"b{i}" for i in range(40) for _ in range(3)])
    folds = cv.assign_folds(groups, n_splits=5)
    for group in set(groups):
        assert len(set(folds[groups == group])) == 1
    assert set(folds) == set(range(5))
    assert len(folds) == len(groups)


def test_too_few_blocks_raises():
    with pytest.raises(ValueError):
        cv.assign_folds(np.array(["a", "b", "c"]), n_splits=5)


def test_regression_metrics():
    truth = np.array([50.0, 60.0, 70.0, 80.0])
    pred = np.array([52.0, 58.0, 73.0, 77.0])
    metrics = cv.regression_metrics(truth, pred)
    assert metrics["n"] == 4
    assert metrics["mae"] == pytest.approx(2.5)
    assert metrics["rmse"] == pytest.approx(math.sqrt((4 + 4 + 9 + 9) / 4))
    assert metrics["spearman"] == pytest.approx(1.0)


def test_regression_metrics_undefine_spearman_on_constant_prediction():
    metrics = cv.regression_metrics([50, 60, 70], [55, 55, 55])
    assert math.isnan(metrics["spearman"])
    assert metrics["mae"] == pytest.approx((5 + 5 + 15) / 3)
