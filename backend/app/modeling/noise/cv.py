"""Spatial block cross-validation helpers (1 km blocks in EPSG:5179)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pyproj import Transformer
from sklearn.model_selection import GroupKFold

BLOCK_SIZE_M = 1000
_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


def block_ids(lng, lat, size_m: int = BLOCK_SIZE_M) -> np.ndarray:
    """1 km block key (ix, iy) for each point; sensors in one block keep one fold."""
    xs, ys = _TO_METRIC.transform(np.asarray(lng, dtype=float), np.asarray(lat, dtype=float))
    ix = np.floor(xs / size_m).astype(int)
    iy = np.floor(ys / size_m).astype(int)
    return np.array([f"{a}_{b}" for a, b in zip(ix, iy)], dtype=object)


def assign_folds(groups: np.ndarray, n_splits: int = 5) -> np.ndarray:
    """Fold id per row via GroupKFold; identical groups always share a fold."""
    unique = sorted(set(groups))
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    if len(unique) < n_splits:
        raise ValueError(f"only {len(unique)} blocks for {n_splits} folds")
    dummy = np.zeros(len(groups))
    splits = GroupKFold(n_splits=n_splits).split(dummy, groups=groups)
    folds = np.empty(len(groups), dtype=int)
    for fold, (_, test_index) in enumerate(splits):
        folds[test_index] = fold
    return folds


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    """MAE (dB), RMSE (dB) and Spearman rho; NaN when undefined."""
    from scipy.stats import spearmanr

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) == 0:
        return {"n": 0, "mae": math.nan, "rmse": math.nan, "spearman": math.nan}
    errors = y_pred - y_true
    if len(y_true) >= 3 and np.ptp(y_true) > 0 and np.ptp(y_pred) > 0:
        rho = float(spearmanr(y_true, y_pred).statistic)
    else:
        rho = math.nan
    return {
        "n": int(len(y_true)),
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "spearman": rho,
    }


def _summary(values: np.ndarray) -> tuple[float, float]:
    values = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if len(values) == 0:
        return math.nan, math.nan
    return float(values.mean()), float(values.std(ddof=0))


def summarize_folds(fold_table: pd.DataFrame, metric: str) -> tuple[float, float]:
    """Mean and population std of one metric over fold rows."""
    return _summary(fold_table[metric].to_numpy())
