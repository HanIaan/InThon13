#!/usr/bin/env python
"""Reproduce the W4 noise experiment end to end.

    ../.venv/bin/python scripts/noise/run_experiment.py

Run from the repo root. The first run downloads the OSM road/rail cache from
Overpass into ``data/raw/osm/`` (one-time, ~10 min); later runs are offline.
Output CSV: ``data/processed/modeling/noise_cv_results.csv``.

Nothing here touches Supabase, the API engine or ``services/precompute.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts.constants import SEOUL_BBOX  # noqa: E402
from app.modeling.noise import experiment, osm  # noqa: E402


def ensure_osm_cache(osm_dir: Path, *, offline: bool) -> Path:
    geojson = osm_dir / "seoul_roads.geojson"
    if geojson.exists():
        print(f"OSM cache ready: {geojson}")
        return geojson
    if offline:
        raise SystemExit(
            f"OSM cache missing at {geojson} and --offline was given.\n"
            "Run without --offline once to fetch it from the Overpass API."
        )
    print(f"Fetching OSM road/rail tiles for {SEOUL_BBOX} into {osm_dir} (one-time) ...")
    osm.fetch_seoul(SEOUL_BBOX, osm_dir)
    geojson = osm.build_geojson(osm_dir)
    print(f"OSM cache built: {geojson}")
    return geojson


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", default=str(ROOT / "data" / "processed"))
    parser.add_argument("--osm-dir", default=str(ROOT / "data" / "raw" / "osm"))
    parser.add_argument(
        "--output", default=str(ROOT / "data" / "processed" / "modeling" / "noise_cv_results.csv")
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--inner-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--offline", action="store_true", help="never call the network")
    args = parser.parse_args()

    geojson = ensure_osm_cache(Path(args.osm_dir), offline=args.offline)
    result = experiment.run_experiment(
        args.processed_dir,
        geojson,
        args.output,
        n_splits=args.folds,
        inner_splits=args.inner_folds,
        seed=args.seed,
    )

    frame = result["frame"]
    print(
        f"dataset: {frame['sensor_id'].nunique()} sensors, {len(frame)} sensor-slot rows, "
        f"{frame['block'].nunique()} blocks"
    )
    print("\nCV summary (mean ± std over folds, all rows):")
    summary = result["summary"]
    print(
        summary[summary.subset == "all"][
            ["model", "mae", "mae_std", "rmse", "spearman"]
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
    print(f"\nResults written to {result['output_csv']}")


if __name__ == "__main__":
    main()
