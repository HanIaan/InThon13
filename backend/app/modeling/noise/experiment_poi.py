"""POI-augmented noise model (B + 상가/공장 근접도) — training and validation.

Candidates (all on the W4 residual scale, target = observed dB - IDW baseline):

* ``A_idw``          production IDW baseline (W4 model A, unchanged).
* ``B_ridge``        W4 model B: IDW + OSM road/rail Ridge, one global model.
* ``B_poi_ridge``    B plus commercial-store and industrial features, fitted
                     separately per time slot so each slot has its own weights.
* ``B_poi_histgb``   same features with HistGradientBoosting and monotonic
                     constraints (more POI stores -> not less noise; farther
                     source -> not more noise).

Validation reuses the W4 design exactly: the same 1 km spatial-block GroupKFold
5-fold split (same frame order, same seed), inner out-of-fold IDW so a sensor never
predicts itself, per-fold/per-subset(roadside, residential, commercial)/per-slot
MAE, RMSE and Spearman, and the KECO 2024 roadside sites as an independent
rank-only check.

Adoption is judged against **B** (not A): at least 4 of 5 folds improve, mean MAE
gain >= 0.2 dB, and the external rank correlation does not get worse.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.modeling.noise import cv, data, experiment, idw, industry, model, osm, poi
from app.modeling.noise import features as spatial

OSM_FEATURES = spatial.feature_names()
POI_FEATURES = poi.feature_names()
INDUSTRIAL_FEATURES = industry.feature_names()
BASE_FEATURES = ["a_pred", *OSM_FEATURES]
POI_MODEL_FEATURES = ["a_pred", *OSM_FEATURES, *POI_FEATURES, *INDUSTRIAL_FEATURES]

MODEL_A = "A_idw"
MODEL_B = "B_ridge"
MODEL_POI_RIDGE = "B_poi_ridge"
MODEL_POI_GB = "B_poi_histgb"
MODELS = (MODEL_A, MODEL_B, MODEL_POI_RIDGE, MODEL_POI_GB)
ROADSIDE_MAX_M = experiment.ROADSIDE_MAX_M

# "Commercial" block: at least this many stores of any kind within 300 m.
COMMERCIAL_MIN_STORES_300M = 300

# Adoption thresholds fixed by the task brief (relative to model B).
ADOPT_MIN_FOLDS = 4
ADOPT_MIN_MAE_GAIN_DB = 0.2

MODEL_ID = "noise_poi_ridge"
MODEL_VERSION = "1.0.0"
CV_SUBSETS = ("all", "roadside", "residential", "commercial")


# ---- dataset -----------------------------------------------------------------
def build_dataset(
    processed_dir: str | Path,
    osm_geojson: str | Path,
    industrial_geojson: str | Path,
    stores_csv: str | Path,
) -> dict:
    """W4 frame plus POI/industrial features and commercial-block flag."""
    frame, lines = experiment.build_dataset(processed_dir, osm_geojson)
    sensors = frame[["sensor_id", "lng", "lat"]].drop_duplicates("sensor_id")
    stores = poi.load_stores(stores_csv)
    poi_features = poi.compute_poi_features(sensors, stores)
    polygons = industry.project_polygons(osm.load_industrial(industrial_geojson))
    industrial_features = industry.compute_industrial_features(sensors, polygons)

    frame = frame.merge(poi_features, on="sensor_id", how="left")
    frame = frame.merge(industrial_features, on="sensor_id", how="left")
    missing = frame[[*POI_FEATURES, *INDUSTRIAL_FEATURES]].isna().any(axis=1)
    if missing.any():
        raise RuntimeError(f"{int(missing.sum())} sensor rows missing POI/industrial features")
    frame["commercial"] = frame["poi_all_300m"] >= COMMERCIAL_MIN_STORES_300M
    return {"frame": frame, "lines": lines, "stores": stores, "polygons": polygons}


# ---- fitting -----------------------------------------------------------------
def _linear_pipeline() -> object:
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1.0))


def linear_to_raw(pipeline, features: list[str]) -> dict:
    """Convert a fitted impute->scale->ridge pipeline into raw-space weights."""
    imputer = pipeline.named_steps["simpleimputer"]
    scaler = pipeline.named_steps["standardscaler"]
    ridge = pipeline.named_steps["ridge"]
    coefficients = np.asarray(ridge.coef_, dtype=float)
    mean = np.asarray(scaler.mean_, dtype=float)
    scale = np.asarray(scaler.scale_, dtype=float)
    weights = coefficients / scale
    intercept = float(ridge.intercept_ - np.sum(coefficients * mean / scale))
    return {
        "intercept": intercept,
        "weights": {name: float(w) for name, w in zip(features, weights)},
        "impute": {name: float(m) for name, m in zip(features, imputer.statistics_)},
    }


def monotonic_constraints(features: list[str]) -> list[int]:
    """+1/-1/0 per feature: more of this must not lower/increase predicted noise."""
    signs = []
    for name in features:
        if name == "a_pred" or name.startswith("poi_") or "area_ratio" in name:
            signs.append(1)
        elif "road_len" in name or name == "rail_ground_len_300m":
            signs.append(1)
        elif name.startswith("nearest_"):
            signs.append(-1)
        else:
            signs.append(0)
    return signs


def _fit_global_ridge(train: pd.DataFrame, residual: np.ndarray, features: list[str]):
    pipeline = _linear_pipeline()
    pipeline.fit(train[features].to_numpy(dtype=float), residual)
    return pipeline


def _fit_slot_ridge(train: pd.DataFrame, residual: np.ndarray, features: list[str]):
    pipelines, raw = {}, {}
    for slot, positions in train.groupby("slot", observed=True).indices.items():
        pipeline = _linear_pipeline()
        pipeline.fit(train[features].to_numpy(dtype=float)[positions], residual[positions])
        pipelines[slot] = pipeline
        raw[slot] = linear_to_raw(pipeline, features)
    return pipelines, raw


def _fit_slot_histgb(train: pd.DataFrame, residual: np.ndarray, features: list[str], seed: int):
    pipelines = {}
    for slot, positions in train.groupby("slot", observed=True).indices.items():
        gb = HistGradientBoostingRegressor(
            max_iter=300,
            learning_rate=0.05,
            max_leaf_nodes=15,
            min_samples_leaf=20,
            l2_regularization=1.0,
            early_stopping=False,
            monotonic_cst=monotonic_constraints(features),
            random_state=seed,
        )
        gb.fit(train[features].to_numpy(dtype=float)[positions], residual[positions])
        pipelines[slot] = gb
    return pipelines


def _predict_per_slot(models: dict, frame: pd.DataFrame, features: list[str], a_pred: np.ndarray) -> np.ndarray:
    """Predict per slot; the baseline column comes from ``a_pred``, not ``frame``."""
    a_pred = np.asarray(a_pred, dtype=float)
    matrix = np.empty((len(frame), len(features)), dtype=float)
    for column, name in enumerate(features):
        matrix[:, column] = a_pred if name == "a_pred" else frame[name].to_numpy(dtype=float)
    predictions = np.full(len(frame), np.nan, dtype=float)
    for slot, positions in frame.groupby("slot", observed=True).indices.items():
        fitted = models.get(slot)
        if fitted is None:
            continue
        predictions[positions] = fitted.predict(matrix[positions])
    return predictions


# ---- metrics -----------------------------------------------------------------
def _subset_masks(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    roadside = frame["roadside"].to_numpy(dtype=bool)
    commercial = frame["commercial"].to_numpy(dtype=bool)
    return {
        "all": np.ones(len(frame), dtype=bool),
        "roadside": roadside,
        "residential": ~roadside,
        "commercial": commercial,
    }


def _metric_rows(model_name: str, fold, subset: str, slot: str, truth, prediction) -> dict:
    metrics = cv.regression_metrics(truth, prediction)
    return {
        "section": "cv_fold",
        "model": model_name,
        "fold": fold,
        "subset": subset,
        "slot": slot,
        "n": metrics["n"],
        "mae": metrics["mae"],
        "rmse": metrics["rmse"],
        "spearman": metrics["spearman"],
        "note": "",
    }


def run_cv(frame: pd.DataFrame, *, n_splits: int = 5, inner_splits: int = 5,
           seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fold-level metrics and per-row predictions for every model.

    Folds come from the same ``cv.assign_folds(frame["block"])`` call W4 used, and
    the frame keeps W4's ordering, so the split is identical to the earlier run.
    """
    folds = cv.assign_folds(frame["block"].to_numpy(), n_splits)
    fold_rows: list[dict] = []
    prediction_rows: list[pd.DataFrame] = []

    for fold in range(n_splits):
        test_mask = folds == fold
        train = frame.loc[~test_mask].reset_index(drop=True)
        test = frame.loc[test_mask].reset_index(drop=True)

        a_train = experiment.out_of_fold_idw(train, inner_splits)
        a_test = idw.idw_predictions(train, test)

        usable_train = np.isfinite(a_train) & np.isfinite(train["noise_db"].to_numpy(dtype=float))
        train_usable = train.loc[usable_train].reset_index(drop=True)
        if len(train_usable) < 50:
            raise RuntimeError(f"fold {fold}: not enough training rows with an IDW neighbour")
        train_usable = train_usable.assign(a_pred=a_train[usable_train])
        residual = train_usable["noise_db"].to_numpy(dtype=float) - train_usable["a_pred"].to_numpy(dtype=float)

        b_model = _fit_global_ridge(train_usable, residual, BASE_FEATURES)
        ridge_models, _ = _fit_slot_ridge(train_usable, residual, POI_MODEL_FEATURES)
        gb_models = _fit_slot_histgb(train_usable, residual, POI_MODEL_FEATURES, seed)

        evaluate = np.isfinite(a_test) & np.isfinite(test["noise_db"].to_numpy(dtype=float))
        evaluated = test.loc[evaluate].reset_index(drop=True)
        a_eval = a_test[evaluate]
        truth = evaluated["noise_db"].to_numpy(dtype=float)

        b_features = np.column_stack([a_eval, evaluated[OSM_FEATURES].to_numpy(dtype=float)])
        predictions = {
            MODEL_A: a_eval,
            MODEL_B: a_eval + b_model.predict(b_features),
            MODEL_POI_RIDGE: a_eval + _predict_per_slot(ridge_models, evaluated, POI_MODEL_FEATURES, a_eval),
            MODEL_POI_GB: a_eval + _predict_per_slot(gb_models, evaluated, POI_MODEL_FEATURES, a_eval),
        }

        prediction_rows.append(
            pd.DataFrame({
                "fold": fold,
                "sensor_id": evaluated["sensor_id"],
                "slot": evaluated["slot"],
                "block": evaluated["block"],
                "truth": truth,
                **predictions,
            })
        )

        slots = evaluated["slot"].to_numpy()
        masks = _subset_masks(evaluated)
        for name in MODELS:
            values = predictions[name]
            for subset, mask in masks.items():
                fold_rows.append(_metric_rows(name, fold, subset, "all", truth[mask], values[mask]))
            for slot in data.SLOT_ORDER:
                slot_mask = slots == slot
                fold_rows.append(_metric_rows(name, fold, "all", slot, truth[slot_mask], values[slot_mask]))

    fold_table = pd.DataFrame(fold_rows)
    predictions_table = pd.concat(prediction_rows, ignore_index=True) if prediction_rows else pd.DataFrame()
    return fold_table, predictions_table


def summarize_cv(fold_table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (name, subset, slot), group in fold_table.groupby(["model", "subset", "slot"], sort=False):
        mae_mean, mae_std = cv.summarize_folds(group, "mae")
        rmse_mean, rmse_std = cv.summarize_folds(group, "rmse")
        rho_mean, rho_std = cv.summarize_folds(group, "spearman")
        rows.append({
            "section": "cv_summary",
            "model": name,
            "fold": "mean",
            "subset": subset,
            "slot": slot,
            "n": int(group["n"].mean()),
            "mae": mae_mean,
            "rmse": rmse_mean,
            "spearman": rho_mean,
            "mae_std": mae_std,
            "rmse_std": rmse_std,
            "spearman_std": rho_std,
            "note": f"mean ± population std over {len(group)} folds",
        })
    return pd.DataFrame(rows)


def fold_improvements(fold_table: pd.DataFrame, model_name: str, *, baseline: str = MODEL_B,
                      subset: str = "all", slot: str = "all") -> dict:
    base = fold_table[(fold_table.model == baseline) & (fold_table.subset == subset) & (fold_table.slot == slot)]
    other = fold_table[(fold_table.model == model_name) & (fold_table.subset == subset) & (fold_table.slot == slot)]
    joined = base[["fold", "mae"]].merge(other[["fold", "mae"]], on="fold", suffixes=("_base", "_new"))
    deltas = joined["mae_base"] - joined["mae_new"]
    return {
        "baseline": baseline,
        "folds": int(len(joined)),
        "folds_improved": int((deltas > 0).sum()),
        "mean_gain_db": float(deltas.mean()),
        "deltas": deltas.tolist(),
    }


# ---- external validation -----------------------------------------------------
def external_site_frame(sites: pd.DataFrame, lines: list, stores: pd.DataFrame,
                        polygons: list) -> pd.DataFrame:
    """One row per external site per slot with the full feature set."""
    points = sites[["site", "lng", "lat"]].rename(columns={"site": "sensor_id"})
    road = spatial.compute_road_rail_features(points, lines).rename(columns={"sensor_id": "site"})
    shop = poi.compute_poi_features(points, stores).rename(columns={"sensor_id": "site"})
    industrial = industry.compute_industrial_features(points, polygons).rename(
        columns={"sensor_id": "site"}
    )
    merged = sites.merge(road, on="site", how="left").merge(shop, on="site", how="left")
    merged = merged.merge(industrial, on="site", how="left")
    merged["commercial"] = merged["poi_all_300m"] >= COMMERCIAL_MIN_STORES_300M
    rows = []
    for slot in data.SLOT_ORDER:
        chunk = merged.copy()
        chunk["slot"] = slot
        rows.append(chunk)
    return pd.concat(rows, ignore_index=True)


def run_external(
    frame: pd.DataFrame,
    lines: list,
    stores: pd.DataFrame,
    polygons: list,
    processed_dir: str | Path,
    *,
    seed: int = 42,
    inner_splits: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Predict at KECO sites from all S-DoT rows and rank-compare per model."""
    external_root = Path(processed_dir).resolve().parent / "raw" / "external" / "keco"
    _, sites = data.load_keco_auto(external_root / "keco_auto.csv", external_root / "keco_sites.csv")
    if sites.empty:
        return pd.DataFrame(), pd.DataFrame(), {}

    a_oof = experiment.out_of_fold_idw(frame, inner_splits)
    usable = np.isfinite(a_oof) & np.isfinite(frame["noise_db"].to_numpy(dtype=float))
    train = frame.loc[usable].reset_index(drop=True)
    train = train.assign(a_pred=a_oof[usable])
    residual = train["noise_db"].to_numpy(dtype=float) - train["a_pred"].to_numpy(dtype=float)
    b_model = _fit_global_ridge(train, residual, BASE_FEATURES)
    ridge_models, ridge_raw = _fit_slot_ridge(train, residual, POI_MODEL_FEATURES)
    gb_models = _fit_slot_histgb(train, residual, POI_MODEL_FEATURES, seed)

    external = external_site_frame(sites, lines, stores, polygons)
    a_pred = idw.idw_predictions(frame, external)
    b_features = np.column_stack([a_pred, external[OSM_FEATURES].to_numpy(dtype=float)])
    predictions = {
        MODEL_A: a_pred,
        MODEL_B: a_pred + b_model.predict(b_features),
        MODEL_POI_RIDGE: a_pred + _predict_per_slot(ridge_models, external, POI_MODEL_FEATURES, a_pred),
        MODEL_POI_GB: a_pred + _predict_per_slot(gb_models, external, POI_MODEL_FEATURES, a_pred),
    }
    external = external.assign(**predictions)
    external = external[np.isfinite(external[MODEL_A])]
    site_pred = external.groupby("site", observed=True)[list(MODELS)].mean().reset_index()
    comparison = sites.merge(site_pred, on="site", how="inner")

    rows, spearman = [], {}
    for name in MODELS:
        metrics = cv.regression_metrics(comparison["measured_db"], comparison[name])
        spearman[name] = metrics["spearman"]
        rows.append({
            "section": "external",
            "model": name,
            "fold": "all_sdot_train",
            "subset": "keco_auto_sites",
            "slot": "all",
            "n": metrics["n"],
            "mae": metrics["mae"],
            "rmse": metrics["rmse"],
            "spearman": metrics["spearman"],
            "note": (
                "KECO 2024 일 소음도 지점 평균 vs 예측 4시간대 평균; 기간·지표 불일치로 "
                "절대값 비교는 참고용, 순위 상관만 판단에 사용"
            ),
        })
    return pd.DataFrame(rows), comparison, {"ridge_raw": ridge_raw, "spearman": spearman}


# ---- decision & artifact -----------------------------------------------------
def decide(fold_table: pd.DataFrame, external: pd.DataFrame) -> dict:
    external_spearman = {}
    if external is not None and not external.empty:
        external_spearman = {
            row["model"]: float(row["spearman"]) for _, row in external.iterrows()
        }
    b_spearman = external_spearman.get(MODEL_B, math.nan)
    verdicts = {}
    for name in (MODEL_POI_RIDGE, MODEL_POI_GB):
        improvement = fold_improvements(fold_table, name, baseline=MODEL_B)
        rho = external_spearman.get(name, math.nan)
        external_ok = bool(
            np.isfinite(b_spearman) and np.isfinite(rho) and rho >= b_spearman
        )
        adopted = (
            improvement["folds_improved"] >= ADOPT_MIN_FOLDS
            and improvement["mean_gain_db"] >= ADOPT_MIN_MAE_GAIN_DB
            and external_ok
        )
        verdicts[name] = {
            "baseline": MODEL_B,
            "folds_improved": improvement["folds_improved"],
            "folds": improvement["folds"],
            "mean_gain_db": improvement["mean_gain_db"],
            "external_spearman_baseline": b_spearman,
            "external_spearman": rho,
            "external_ok": external_ok,
            "adopted": adopted,
        }
    adopted = [name for name, info in verdicts.items() if info["adopted"]]
    return {
        "models": verdicts,
        "adopted": adopted,
        "recommendation": f"{adopted[0]} 채택 후보" if adopted else "B 유지",
    }


def decision_rows(verdict: dict) -> pd.DataFrame:
    rows = []
    for name, info in verdict["models"].items():
        rows.append({
            "section": "decision", "model": name, "fold": "adoption_check",
            "subset": "all", "slot": "all", "n": info["folds"],
            "mae": info["mean_gain_db"], "rmse": math.nan,
            "spearman": info["external_spearman"],
            "note": (
                f"B 대비 fold 개선 {info['folds_improved']}/{info['folds']}, 평균 MAE 개선 "
                f"{info['mean_gain_db']:.3f} dB, 외부 Spearman B={info['external_spearman_baseline']:.3f} "
                f"-> {info['external_spearman']:.3f} (비악화={info['external_ok']}) => "
                f"{'채택 후보' if info['adopted'] else '미채택'}"
            ),
        })
    rows.append({
        "section": "decision", "model": "overall", "fold": "recommendation",
        "subset": "all", "slot": "all", "n": math.nan, "mae": math.nan,
        "rmse": math.nan, "spearman": math.nan, "note": verdict["recommendation"],
    })
    return pd.DataFrame(rows)


def coefficient_table(slot_models: dict) -> pd.DataFrame:
    """Learned per-slot weights in raw units (dB per store / metre / ratio)."""
    rows = []
    for slot, config in slot_models.items():
        for name, weight in config["weights"].items():
            rows.append({
                "slot": slot,
                "feature": name,
                "weight_db_per_unit": weight,
                "impute_median": config["impute"][name],
            })
    return pd.DataFrame(rows)


def feature_descriptions() -> list[dict]:
    entries = [{
        "name": "a_pred",
        "kind": "baseline",
        "description": "IDW baseline of model A at the point (per time slot); primary 1 km, fallback 5 km radius",
    }]
    for name in OSM_FEATURES:
        kind = "rail" if "rail" in name else "road"
        entries.append({"name": name, "kind": kind,
                        "description": "OSM road/rail feature in EPSG:5179 metres (W4 definition)"})
    for name in POI_FEATURES:
        if name.startswith("poi_"):
            category, radius = name[len("poi_"):].rsplit("_", 1)
            entries.append({
                "name": name, "kind": "poi",
                "description": f"{category} store count within {radius} (상가업소 2026-09-30)",
            })
        else:
            category = name[len("nearest_"):-2]
            entries.append({
                "name": name, "kind": "poi",
                "description": f"distance to nearest {category} store in metres, capped at {poi.DEFAULT_CAP_M:g}",
            })
    for name in INDUSTRIAL_FEATURES:
        entries.append({
            "name": name, "kind": "industrial",
            "description": "OSM industrial footprint area ratio / nearest distance (EPSG:5179 metres)",
        })
    return entries


def build_model_artifact(
    *,
    frame: pd.DataFrame,
    slot_models: dict,
    fold_table: pd.DataFrame,
    created_at: str,
    source_notes: dict,
    adoption: dict | None = None,
) -> dict:
    """Export the adopted per-slot Ridge as a served artifact dictionary."""
    summary = summarize_cv(fold_table)
    ridge = summary[(summary.model == MODEL_POI_RIDGE) & (summary.subset == "all")
                    & (summary.slot != "all")]
    slot_cv = {
        row.slot: {
            "mae": float(row.mae),
            "mae_std": float(row.mae_std),
            "rmse": float(row.rmse),
            "spearman": float(row.spearman),
            "n": int(row.n),
        }
        for row in ridge.itertuples()
    }
    slots = {}
    for slot, config in slot_models.items():
        slots[slot] = {
            "intercept": config["intercept"],
            "weights": config["weights"],
            "impute": config["impute"],
            "cv": slot_cv.get(slot, {}),
        }
    training = {
        "period_start": str(source_notes.get("period_start")),
        "period_end": str(source_notes.get("period_end")),
        "sdot_rows": int(frame["sensor_id"].nunique()),
        "sensor_slot_rows": int(len(frame)),
        "stores_snapshot": source_notes.get("stores_snapshot"),
        "stores_rows": source_notes.get("stores_rows"),
        "osm_cache": source_notes.get("osm_cache"),
        "industrial_cache": source_notes.get("industrial_cache"),
        "target": "S-DoT AVG_NIS time-slot mean dB",
        "hyperparameters": {
            "model": "Ridge",
            "ridge_alpha": 1.0,
            "per_time_slot": True,
            "preprocessing": "median imputation then z-score standardization",
            "target_form": "residual = observed dB - IDW baseline (a_pred)",
        },
        "cv": {
            "scheme": "1km spatial block GroupKFold",
            "folds": int(fold_table["fold"].nunique()),
            "seed": source_notes.get("seed", 42),
            "commercial_min_stores_300m": COMMERCIAL_MIN_STORES_300M,
        },
    }
    metrics = {
        "cv_summary_all": {
            row.model: {
                "mae": float(row.mae), "mae_std": float(row.mae_std),
                "rmse": float(row.rmse), "spearman": float(row.spearman),
            }
            for row in summary[(summary.subset == "all") & (summary.slot == "all")].itertuples()
        }
    }
    return model.build_artifact(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        created_at=created_at,
        training=training,
        features=feature_descriptions(),
        slot_models=slots,
        metrics=metrics,
        adoption=adoption,
    )


def run_experiment(
    processed_dir: str | Path,
    osm_geojson: str | Path,
    industrial_geojson: str | Path,
    stores_csv: str | Path,
    output_csv: str | Path,
    *,
    artifact_path: str | Path | None = None,
    coefficients_csv: str | Path | None = None,
    created_at: str | None = None,
    stores_snapshot: str = "2026-09-30",
    n_splits: int = 5,
    inner_splits: int = 5,
    seed: int = 42,
) -> dict:
    from datetime import datetime

    built = build_dataset(processed_dir, osm_geojson, industrial_geojson, stores_csv)
    frame, lines, stores, polygons = built["frame"], built["lines"], built["stores"], built["polygons"]

    fold_table, predictions = run_cv(frame, n_splits=n_splits, inner_splits=inner_splits, seed=seed)
    summary = summarize_cv(fold_table)
    external, comparison, external_extra = run_external(
        frame, lines, stores, polygons, processed_dir, seed=seed, inner_splits=inner_splits
    )
    verdict = decide(fold_table, external)
    decisions = decision_rows(verdict)

    results = pd.concat([fold_table, summary, external, decisions], ignore_index=True)
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_csv, index=False, encoding="utf-8")

    # Final model trained on every usable sensor-slot row (served artifact).
    a_oof = experiment.out_of_fold_idw(frame, inner_splits)
    usable = np.isfinite(a_oof) & np.isfinite(frame["noise_db"].to_numpy(dtype=float))
    train = frame.loc[usable].reset_index(drop=True).assign(a_pred=a_oof[usable])
    residual = train["noise_db"].to_numpy(dtype=float) - train["a_pred"].to_numpy(dtype=float)
    _, ridge_raw = _fit_slot_ridge(train, residual, POI_MODEL_FEATURES)
    coefficients = coefficient_table(ridge_raw)
    if coefficients_csv is not None:
        coefficients_csv = Path(coefficients_csv)
        coefficients_csv.parent.mkdir(parents=True, exist_ok=True)
        coefficients.to_csv(coefficients_csv, index=False, encoding="utf-8")

    observations = pd.read_csv(Path(processed_dir) / "sdot_observations.csv", usecols=["observed_at"])
    stamps = pd.to_datetime(observations["observed_at"], utc=True, errors="coerce").dropna()
    source_notes = {
        "period_start": stamps.min().isoformat() if len(stamps) else None,
        "period_end": stamps.max().isoformat() if len(stamps) else None,
        "stores_snapshot": stores_snapshot,
        "stores_rows": int(len(stores)),
        "osm_cache": str(Path(osm_geojson).name),
        "industrial_cache": str(Path(industrial_geojson).name),
        "seed": seed,
    }
    artifact = build_model_artifact(
        frame=frame,
        slot_models=ridge_raw,
        fold_table=fold_table,
        created_at=created_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        source_notes=source_notes,
        adoption={
            "status": "adopted" if MODEL_POI_RIDGE in verdict["adopted"] else "not_adopted",
            "recommendation": verdict["recommendation"],
            "baseline": MODEL_B,
            "criteria": {
                "min_folds_improved": ADOPT_MIN_FOLDS,
                "min_mean_gain_db": ADOPT_MIN_MAE_GAIN_DB,
                "external_rank_must_not_worsen": True,
            },
            "result": verdict["models"].get(MODEL_POI_RIDGE, {}),
        },
    )
    written = model.write_artifact(artifact_path, artifact) if artifact_path is not None else None

    return {
        "frame": frame,
        "fold_table": fold_table,
        "predictions": predictions,
        "summary": summary,
        "external": external,
        "external_comparison": comparison,
        "verdict": verdict,
        "results": results,
        "coefficients": coefficients,
        "artifact": artifact,
        "artifact_path": written,
        "output_csv": output_csv,
        "coefficients_csv": coefficients_csv,
        "feature_importances": external_extra.get("ridge_raw"),
    }
