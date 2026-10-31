#!/usr/bin/env python
"""Export W4 model B (IDW + OSM road/rail Ridge) as a served noise artifact.

    ../.venv/bin/python ../scripts/noise/train_road_model.py      # from backend/

Re-runs the W4 spatial-block CV (same folds, same seed) so the artifact carries its
own per-slot CV MAE, then fits the same global Ridge on every usable sensor-slot row
and writes ``backend/app/modeling/noise/artifacts/noise_road_model_v1.json``. The
global model is copied to every slot so the artifact keeps the per-slot schema.

Reads only the processed S-DoT files and the cached OSM roads; touches no database.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.modeling.noise import cv, data, experiment, model  # noqa: E402
from app.modeling.noise.experiment_poi import linear_to_raw  # noqa: E402

MODEL_ID = "noise_road_ridge"
MODEL_VERSION = "1.0.0"
ARTIFACT = ROOT / "backend" / "app" / "modeling" / "noise" / "artifacts" / "noise_road_model_v1.json"


def slot_cv(predictions: pd.DataFrame) -> dict:
    """Per-slot CV metrics of model B, pooled over the out-of-fold predictions."""
    output = {}
    for slot in data.SLOT_ORDER:
        rows = predictions[predictions["slot"] == slot]
        metrics = cv.regression_metrics(rows["truth"].to_numpy(), rows[experiment.MODEL_RIDGE].to_numpy())
        fold_mae = [
            cv.regression_metrics(group["truth"].to_numpy(), group[experiment.MODEL_RIDGE].to_numpy())["mae"]
            for _, group in rows.groupby("fold")
        ]
        output[slot] = {
            "mae": float(metrics["mae"]),
            "mae_std": float(np.std(fold_mae)),
            "rmse": float(metrics["rmse"]),
            "spearman": float(metrics["spearman"]),
            "n": int(metrics["n"]),
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", default=str(ROOT / "data" / "processed"))
    parser.add_argument("--osm-geojson", default=str(ROOT / "data" / "raw" / "osm" / "seoul_roads.geojson"))
    parser.add_argument("--artifact", default=str(ARTIFACT))
    args = parser.parse_args()

    frame, _ = experiment.build_dataset(args.processed_dir, args.osm_geojson)
    fold_table, predictions = experiment.run_cv(frame)
    summary = experiment.summarize_cv(fold_table)
    overall = summary[(summary["subset"] == "all")].set_index("model")

    a_oof = experiment.out_of_fold_idw(frame)
    usable = np.isfinite(a_oof) & np.isfinite(frame["noise_db"].to_numpy(dtype=float))
    train = frame.loc[usable].reset_index(drop=True).assign(a_pred=a_oof[usable])
    residual = train["noise_db"].to_numpy(dtype=float) - train["a_pred"].to_numpy(dtype=float)
    fitted = experiment._fit_residual_models(train, residual, seed=42)[experiment.MODEL_RIDGE]
    raw = linear_to_raw(fitted, experiment.MODEL_FEATURES)

    per_slot = slot_cv(predictions)
    observations = pd.read_csv(Path(args.processed_dir) / "sdot_observations.csv", usecols=["observed_at"])
    stamps = pd.to_datetime(observations["observed_at"], utc=True, errors="coerce").dropna()
    features = [{"name": "a_pred", "kind": "baseline",
                 "description": "IDW baseline of model A (1 km, up to 8 sensors, 1/d^2)"}]
    features += [{"name": name, "kind": "road", "description": "OSM road/rail feature in EPSG:5179 metres (W4 definition)"}
                 for name in experiment.OSM_FEATURES]
    artifact = model.build_artifact(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        training={
            "period_start": stamps.min().isoformat(),
            "period_end": stamps.max().isoformat(),
            "sdot_rows": int(frame["sensor_id"].nunique()),
            "sensor_slot_rows": int(len(frame)),
            "osm_cache": Path(args.osm_geojson).name,
            "target": "S-DoT AVG_NIS time-slot mean dB",
            "hyperparameters": {
                "model": "Ridge", "ridge_alpha": 1.0, "per_time_slot": False,
                "preprocessing": "median imputation then z-score standardization",
                "target_form": "residual = observed dB - IDW baseline (a_pred)",
            },
            "cv": {"scheme": "1km spatial block GroupKFold", "folds": 5, "seed": 42},
        },
        features=features,
        slot_models={slot: {**raw, "cv": per_slot[slot]} for slot in data.SLOT_ORDER},
        # Cells with no sensor within 1 km stay missing: the model never extrapolates further.
        baseline={"primary": {"max_distance_m": 1000.0, "k": 8, "power": 2.0}},
        metrics={"cv_summary_all": {name: {"mae": float(row.mae), "mae_std": float(row.mae_std),
                                           "rmse": float(row.rmse), "spearman": float(row.spearman)}
                                    for name, row in overall.iterrows()}},
        adoption={"status": "adopted", "baseline": experiment.MODEL_A,
                  "evidence": "docs/research/noise-model-evaluation.md (W4: 5/5 folds, KECO rank -0.12 -> 0.21)"},
    )
    written = model.write_artifact(args.artifact, artifact)
    print(overall[["mae", "mae_std", "rmse", "spearman"]].round(3).to_string())
    print({slot: round(v["mae"], 3) for slot, v in per_slot.items()})
    print(f"artifact: {written}")


if __name__ == "__main__":
    main()
