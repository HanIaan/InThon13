#!/usr/bin/env python
"""Reproduce the POI-augmented noise experiment (B + 상가/공장 근접도).

    ../.venv/bin/python scripts/noise/run_poi_experiment.py \
        --stores-csv <stores_seoul_202609.csv>

Run from the repo root. Requires the W4 OSM road/rail cache and the industrial
cache (``scripts/noise/fetch_industrial.py``); nothing here touches Supabase, the
API engine or the Published snapshot.

Outputs (all except the artifact are git-ignored):
* ``data/processed/modeling/noise_poi_cv_results.csv``  — fold/summary/external/decision rows
* ``data/processed/modeling/noise_poi_coefficients.csv`` — learned weights per slot
* ``backend/app/modeling/noise/artifacts/noise_poi_model_v1.json`` — served artifact
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.modeling.noise import experiment_poi  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", default=str(ROOT / "data" / "processed"))
    parser.add_argument("--osm-dir", default=str(ROOT / "data" / "raw" / "osm"))
    parser.add_argument(
        "--stores-csv",
        default=os.environ.get("NOISE_POI_STORES_CSV", ""),
        help="상가(상권)정보 CSV (default: $NOISE_POI_STORES_CSV)",
    )
    parser.add_argument(
        "--output", default=str(ROOT / "data" / "processed" / "modeling" / "noise_poi_cv_results.csv")
    )
    parser.add_argument(
        "--coefficients",
        default=str(ROOT / "data" / "processed" / "modeling" / "noise_poi_coefficients.csv"),
    )
    parser.add_argument(
        "--artifact",
        default=str(
            ROOT / "backend" / "app" / "modeling" / "noise" / "artifacts" / "noise_poi_model_v1.json"
        ),
    )
    parser.add_argument("--created-at", default=None, help="artifact timestamp override (ISO-8601)")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--inner-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.stores_csv:
        raise SystemExit(
            "Pass --stores-csv <stores_seoul_202609.csv> or set NOISE_POI_STORES_CSV."
        )
    osm_dir = Path(args.osm_dir)
    industrial = osm_dir / "industrial_areas.geojson"
    roads = osm_dir / "seoul_roads.geojson"
    for path in (roads, industrial):
        if not path.exists():
            raise SystemExit(f"missing OSM cache: {path}")

    result = experiment_poi.run_experiment(
        args.processed_dir,
        roads,
        industrial,
        args.stores_csv,
        args.output,
        artifact_path=args.artifact,
        coefficients_csv=args.coefficients,
        created_at=args.created_at,
        n_splits=args.folds,
        inner_splits=args.inner_folds,
        seed=args.seed,
    )

    frame = result["frame"]
    print(
        f"dataset: {frame['sensor_id'].nunique()} sensors, {len(frame)} sensor-slot rows, "
        f"{frame['block'].nunique()} blocks, commercial rows "
        f"{int(frame['commercial'].sum())}"
    )
    print("\nCV summary (mean ± std, subset=all, slot=all):")
    summary = result["summary"]
    print(
        summary[(summary.subset == "all") & (summary.slot == "all")][
            ["model", "mae", "mae_std", "rmse", "spearman"]
        ].to_string(index=False, float_format=lambda v: f"{v:.3f}")
    )
    print("\nCV by time slot (subset=all):")
    print(
        summary[(summary.subset == "all") & (summary.slot != "all")][
            ["model", "slot", "mae", "rmse", "spearman"]
        ].to_string(index=False, float_format=lambda v: f"{v:.3f}")
    )
    print("\nExternal (KECO 2024 roadside sites, rank only):")
    external = result["external"]
    if external.empty:
        print("  no external sites with coordinates")
    else:
        print(external[["model", "n", "mae", "spearman"]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\nDecision:")
    print(json.dumps(result["verdict"], ensure_ascii=False, indent=2))
    print(f"\nResults: {result['output_csv']}")
    print(f"Coefficients: {result['coefficients_csv']}")
    print(f"Artifact: {result['artifact_path']}")


if __name__ == "__main__":
    main()
