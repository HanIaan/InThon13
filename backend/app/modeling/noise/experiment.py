"""W4 noise model experiment: A (IDW) vs B (IDW + OSM residual correction).

Pipeline (all offline once the OSM cache exists):

1. Target = sensor x time_slot mean of usable hourly S-DoT noise values.
2. Features = OSM nearest-road distances / road lengths / rail distances.
3. Outer validation = 5-fold GroupKFold over 1 km spatial blocks.
4. Model A = production IDW rules, rebuilt from the training fold only.
5. Model B = A prediction + OSM features predicting A's residual; the residual
   model is trained on inner out-of-fold IDW predictions so a sensor never
   predicts itself during training.
6. External check = rank correlation against KECO 2024 roadside stations (never
   used for training; periods and metrics differ, so only ranks are compared).

Model C (TOPIS traffic volumes) is not run: it needs SEOUL_OPEN_API_KEY, which is
not available for this task. Model D (KECO/Seoul networks as training data) is
deliberately not run: unit compatibility with S-DoT AVG_NIS is unconfirmed.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.modeling.noise import cv, data, idw, osm
from app.modeling.noise import features as spatial

OSM_FEATURES = spatial.feature_names()
MODEL_FEATURES = ["a_pred", *OSM_FEATURES]
MODEL_A = "A_idw"
MODEL_RIDGE = "B_ridge"
MODEL_GB = "B_gb"
MODELS = (MODEL_A, MODEL_RIDGE, MODEL_GB)
ROADSIDE_MAX_M = 50.0

# Adoption thresholds fixed by the task brief.
ADOPT_MIN_FOLDS = 4
ADOPT_MIN_MAE_GAIN_DB = 0.3


def build_dataset(processed_dir: str | Path, osm_geojson: str | Path) -> tuple[pd.DataFrame, list]:
    """Sensor x slot target rows joined with OSM spatial features and block keys."""
    slot_rows = data.load_sdot_slot_means(processed_dir)
    lines = spatial.project_lines(osm.load_features(osm_geojson))
    sensors = slot_rows[["sensor_id", "lng", "lat"]].drop_duplicates("sensor_id")
    feature_table = spatial.compute_road_rail_features(sensors, lines)
    frame = slot_rows.merge(feature_table, on="sensor_id", how="left")
    frame = frame.dropna(subset=["lng", "lat"]).reset_index(drop=True)
    frame["block"] = cv.block_ids(frame["lng"], frame["lat"])
    frame["roadside"] = frame["nearest_major_road_m"] <= ROADSIDE_MAX_M
    return frame, lines


def out_of_fold_idw(frame: pd.DataFrame, n_splits: int = 5) -> np.ndarray:
    """IDW prediction for each row from other blocks (no self-prediction)."""
    predictions = np.full(len(frame), np.nan, dtype=float)
    if len(frame) == 0:
        return predictions
    folds = cv.assign_folds(frame["block"].to_numpy(), n_splits)
    for fold in range(n_splits):
        test = folds == fold
        if not test.any():
            continue
        predictions[test] = idw.idw_predictions(frame[~test], frame[test])
    return predictions


def _fit_residual_models(frame: pd.DataFrame, residual: np.ndarray, seed: int):
    x = frame[MODEL_FEATURES].to_numpy(dtype=float)
    ridge = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1.0))
    ridge.fit(x, residual)
    gb = GradientBoostingRegressor(
        n_estimators=300,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        random_state=seed,
    )
    gb.fit(x, residual)
    return {MODEL_RIDGE: ridge, MODEL_GB: gb}


def _subset_masks(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    roadside = frame["roadside"].to_numpy(dtype=bool)
    return {"all": np.ones(len(frame), dtype=bool), "roadside": roadside,
            "residential": ~roadside}


def _metric_row(model: str, fold, subset: str, truth, prediction, note: str = "") -> dict:
    metrics = cv.regression_metrics(truth, prediction)
    return {
        "section": "cv_fold" if fold is not None else "cv_summary",
        "model": model,
        "fold": fold,
        "subset": subset,
        "n": metrics["n"],
        "mae": metrics["mae"],
        "rmse": metrics["rmse"],
        "spearman": metrics["spearman"],
        "note": note,
    }


def run_cv(frame: pd.DataFrame, *, n_splits: int = 5, inner_splits: int = 5,
           seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fold-level CV rows plus per-fold model predictions (for paired deltas)."""
    folds = cv.assign_folds(frame["block"].to_numpy(), n_splits)
    fold_rows: list[dict] = []
    prediction_rows: list[pd.DataFrame] = []

    for fold in range(n_splits):
        test_mask = folds == fold
        train = frame.loc[~test_mask].reset_index(drop=True)
        test = frame.loc[test_mask].reset_index(drop=True)

        a_train = out_of_fold_idw(train, inner_splits)
        a_test = idw.idw_predictions(train, test)

        usable_train = np.isfinite(a_train) & np.isfinite(train["noise_db"].to_numpy(dtype=float))
        train_usable = train.loc[usable_train].reset_index(drop=True)
        train_usable["a_pred"] = a_train[usable_train]
        if len(train_usable) < 20:
            raise RuntimeError(f"fold {fold}: not enough training rows with an IDW neighbour")
        residual = train_usable["noise_db"].to_numpy(dtype=float) - train_usable["a_pred"].to_numpy(dtype=float)
        models = _fit_residual_models(train_usable, residual, seed)

        # Only rows with a finite IDW baseline and a target are comparable across models.
        evaluate = np.isfinite(a_test) & np.isfinite(test["noise_db"].to_numpy(dtype=float))
        evaluated = test.loc[evaluate].reset_index(drop=True)
        a_eval = a_test[evaluate]
        truth = evaluated["noise_db"].to_numpy(dtype=float)

        test_features = np.column_stack(
            [a_eval, evaluated[OSM_FEATURES].to_numpy(dtype=float)]
        )
        predictions = {MODEL_A: a_eval}
        for name, model in models.items():
            predictions[name] = a_eval + model.predict(test_features)

        masks = _subset_masks(evaluated)

        prediction_rows.append(
            pd.DataFrame(
                {
                    "fold": fold,
                    "sensor_id": evaluated["sensor_id"],
                    "slot": evaluated["slot"],
                    "truth": truth,
                    **{name: values for name, values in predictions.items()},
                }
            )
        )
        for name in MODELS:
            values = predictions[name]
            for subset, mask in masks.items():
                fold_rows.append(_metric_row(name, fold, subset, truth[mask], values[mask]))

    fold_table = pd.DataFrame(fold_rows)
    predictions_table = pd.concat(prediction_rows, ignore_index=True) if prediction_rows else pd.DataFrame()
    return fold_table, predictions_table


def summarize_cv(fold_table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (model, subset), group in fold_table.groupby(["model", "subset"], sort=False):
        mae_mean, mae_std = cv.summarize_folds(group, "mae")
        rmse_mean, rmse_std = cv.summarize_folds(group, "rmse")
        rho_mean, rho_std = cv.summarize_folds(group, "spearman")
        rows.append(
            {
                "section": "cv_summary",
                "model": model,
                "fold": "mean",
                "subset": subset,
                "n": int(group["n"].mean()),
                "mae": mae_mean,
                "rmse": rmse_mean,
                "spearman": rho_mean,
                "mae_std": mae_std,
                "rmse_std": rmse_std,
                "spearman_std": rho_std,
                "note": f"mean ± population std over {len(group)} folds",
            }
        )
    return pd.DataFrame(rows)


def fold_improvements(fold_table: pd.DataFrame, model: str, subset: str = "all") -> dict:
    base = fold_table[(fold_table.model == MODEL_A) & (fold_table.subset == subset)]
    other = fold_table[(fold_table.model == model) & (fold_table.subset == subset)]
    joined = base[["fold", "mae"]].merge(other[["fold", "mae"]], on="fold", suffixes=("_a", "_b"))
    deltas = joined["mae_a"] - joined["mae_b"]
    return {
        "folds": int(len(joined)),
        "folds_improved": int((deltas > 0).sum()),
        "mean_gain_db": float(deltas.mean()),
        "deltas": deltas.tolist(),
    }


# ---- External validation (independent measurements, rank comparison only) ----
def external_site_frame(sites: pd.DataFrame, lines: list) -> pd.DataFrame:
    """One row per external site with OSM features; one row per slot for prediction."""
    points = sites[["site", "lng", "lat"]].rename(columns={"site": "sensor_id"})
    features = spatial.compute_road_rail_features(points, lines)
    features = features.rename(columns={"sensor_id": "site"})
    merged = sites.merge(features, on="site", how="left")
    rows = []
    for slot in data.SLOT_ORDER:
        chunk = merged.copy()
        chunk["slot"] = slot
        rows.append(chunk)
    return pd.concat(rows, ignore_index=True)


def run_external(
    frame: pd.DataFrame,
    lines: list,
    processed_dir: str | Path,
    *,
    seed: int = 42,
    inner_splits: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Predict at KECO sites from a model trained on all S-DoT rows, then rank-compare."""
    external_root = Path(processed_dir).resolve().parent / "raw" / "external" / "keco"
    auto_path = external_root / "keco_auto.csv"
    sites_path = external_root / "keco_sites.csv"
    _, sites = data.load_keco_auto(auto_path, sites_path)
    if sites.empty:
        return pd.DataFrame(), pd.DataFrame()

    a_oof = out_of_fold_idw(frame, inner_splits)
    usable = np.isfinite(a_oof) & np.isfinite(frame["noise_db"].to_numpy(dtype=float))
    train = frame.loc[usable].reset_index(drop=True)
    train["a_pred"] = a_oof[usable]
    residual = train["noise_db"].to_numpy(dtype=float) - train["a_pred"].to_numpy(dtype=float)
    models = _fit_residual_models(train, residual, seed)

    external = external_site_frame(sites, lines)
    a_pred = idw.idw_predictions(frame, external)
    features = external[OSM_FEATURES].copy()
    features["a_pred"] = a_pred
    features = features[MODEL_FEATURES]
    predictions = {MODEL_A: a_pred}
    for name, model in models.items():
        predictions[name] = a_pred + model.predict(features.to_numpy(dtype=float))

    external = external.assign(**{name: values for name, values in predictions.items()})
    external = external[np.isfinite(external[MODEL_A])]
    # Site-level prediction: equal weight across slots (KECO publishes no hour detail).
    site_pred = (
        external.groupby("site", observed=True)[list(MODELS)]
        .mean()
        .reset_index()
    )
    comparison = sites.merge(site_pred, on="site", how="inner")
    per_slot = external[["site", "slot", *MODELS]].copy()

    rows = []
    for name in MODELS:
        metrics = cv.regression_metrics(comparison["measured_db"], comparison[name])
        rows.append(
            {
                "section": "external",
                "model": name,
                "fold": "all_sdot_train",
                "subset": "keco_auto_sites",
                "n": metrics["n"],
                "mae": metrics["mae"],
                "rmse": metrics["rmse"],
                "spearman": metrics["spearman"],
                "note": (
                    "KECO 2024 일 소음도 지점 평균 vs 예측 4시간대 평균; 기간·지표 불일치로 "
                    "절대값 비교는 참고용, 순위 상관만 판단에 사용"
                ),
            }
        )
    return pd.DataFrame(rows), comparison


def decide(fold_table: pd.DataFrame, external: pd.DataFrame) -> dict:
    verdicts = {}
    has_external = external is not None and not external.empty
    for name in (MODEL_RIDGE, MODEL_GB):
        improvement = fold_improvements(fold_table, name)
        if has_external:
            ext_a = external[(external.model == MODEL_A)]
            ext_b = external[(external.model == name)]
            spearman_a = float(ext_a["spearman"].iloc[0]) if len(ext_a) and np.isfinite(ext_a["spearman"].iloc[0]) else math.nan
            spearman_b = float(ext_b["spearman"].iloc[0]) if len(ext_b) and np.isfinite(ext_b["spearman"].iloc[0]) else math.nan
        else:
            spearman_a = spearman_b = math.nan
        external_ok = bool(np.isfinite(spearman_a) and np.isfinite(spearman_b) and spearman_b >= spearman_a)
        adopted = (
            improvement["folds_improved"] >= ADOPT_MIN_FOLDS
            and improvement["mean_gain_db"] >= ADOPT_MIN_MAE_GAIN_DB
            and external_ok
        )
        verdicts[name] = {
            "folds_improved": improvement["folds_improved"],
            "folds": improvement["folds"],
            "mean_gain_db": improvement["mean_gain_db"],
            "external_spearman_a": spearman_a,
            "external_spearman_b": spearman_b,
            "external_ok": external_ok,
            "adopted": adopted,
        }
    any_adopted = any(v["adopted"] for v in verdicts.values())
    return {"models": verdicts, "recommendation": "B 채택 후보" if any_adopted else "A 유지"}


def decision_rows(fold_table: pd.DataFrame, external: pd.DataFrame, verdict: dict) -> pd.DataFrame:
    rows = []
    for name, info in verdict["models"].items():
        rows.append(
            {
                "section": "decision",
                "model": name,
                "fold": "adoption_check",
                "subset": "all",
                "n": info["folds"],
                "mae": info["mean_gain_db"],
                "rmse": math.nan,
                "spearman": info["external_spearman_b"],
                "note": (
                    f"fold 개선 {info['folds_improved']}/{info['folds']}, 평균 MAE 개선 "
                    f"{info['mean_gain_db']:.3f} dB, 외부 Spearman A={info['external_spearman_a']:.3f} "
                    f"B={info['external_spearman_b']:.3f} (비악화={info['external_ok']}) -> "
                    f"{'채택 후보' if info['adopted'] else '미채택'}"
                ),
            }
        )
    rows.append(
        {
            "section": "decision",
            "model": "overall",
            "fold": "recommendation",
            "subset": "all",
            "n": math.nan,
            "mae": math.nan,
            "rmse": math.nan,
            "spearman": math.nan,
            "note": verdict["recommendation"],
        }
    )
    return pd.DataFrame(rows)


def run_experiment(
    processed_dir: str | Path,
    osm_geojson: str | Path,
    output_csv: str | Path,
    *,
    n_splits: int = 5,
    inner_splits: int = 5,
    seed: int = 42,
) -> dict:
    frame, lines = build_dataset(processed_dir, osm_geojson)
    fold_table, predictions = run_cv(
        frame, n_splits=n_splits, inner_splits=inner_splits, seed=seed
    )
    summary = summarize_cv(fold_table)
    external, comparison = run_external(
        frame, lines, processed_dir, seed=seed, inner_splits=inner_splits
    )
    verdict = decide(fold_table, external)
    decisions = decision_rows(fold_table, external, verdict)

    results = pd.concat([fold_table, summary, external, decisions], ignore_index=True)
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_csv, index=False, encoding="utf-8")

    return {
        "frame": frame,
        "fold_table": fold_table,
        "predictions": predictions,
        "summary": summary,
        "external": external,
        "external_comparison": comparison,
        "verdict": verdict,
        "results": results,
        "output_csv": output_csv,
    }
