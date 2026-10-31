"""Train / validate the L1 dasymetric light model and write its versioned artifact.

Run from ``backend/``::

    ../.venv/bin/python -m app.modeling.light.train \
        --stores /Users/iaan/.superset/worktrees/Inthon/shared-data/stores/stores_seoul_202609.csv

Scale-transfer validation (the only observed target finer than the block aggregation):

1. Aggregate NASA VNP46A3 463 m pixels into full 3x3 blocks (~1.4 km), B = mean radiance.
2. Redistribute B back to the nine 463 m pixels with ``w = exp(sum_k beta_k z_k)`` and the
   exact within-block mean normalisation used by the 200 m application.
3. Compare with the observed 463 m radiance. Baseline L0 is uniform B inside the block.

``beta`` is fitted on block-centred, globally standardised features. Block centring makes
the within-block normalisation constant orthogonal to the design, so ridge recovers the
log-linear model coefficients without needing the normalisation term in the objective.
Folds are spatial super-blocks (15 x 15 pixels, ~7 km) so train and validation never share
a 1 km neighbourhood.

The artifact embeds the per-cell weight exponent for every 200 m Seoul cell, which makes
inference (:mod:`app.modeling.light.model`) depend on this JSON alone.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from app.contracts.constants import (
    GRID_CELL_SIZE_M, GRID_ORIGIN_X, GRID_ORIGIN_Y, WGS84, GRID_CRS,
)
from app.contracts.data_interfaces import NightLightRecord
from app.core import grid
from app.modeling.light.features import (
    FEATURE_NAMES, FEATURE_SPECS, FeatureSource, project_points, transform_features,
)
from app.modeling.light.model import (
    METHOD, DominantPixelLayer, dasymetric_values, is_usable,
)

KST = ZoneInfo("Asia/Seoul")
_TO_METRIC = Transformer.from_crs(WGS84, GRID_CRS, always_xy=True)

DEFAULT_ARTIFACT = Path(__file__).parent / "artifacts" / "light_poi_model_v1.json"
DEFAULT_PROCESSED = Path("../data/processed")
DEFAULT_ROADS = Path("../data/raw/osm/seoul_roads.geojson")
DEFAULT_INDUSTRIAL = Path("../data/raw/osm/seoul_industrial.geojson")
ALPHAS = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
FOLDS = 5
SUPERBLOCK = 5  # 5 blocks x 3 pixels = 15 pixels ~= 7 km spatial fold unit
NIGHT_HOURS = frozenset({22, 23, 0, 1})
MIN_NIGHT_SAMPLES = 12


# ---------------------------------------------------------------- geometry


def metric_cell(ix: int, iy: int):
    x, y = GRID_ORIGIN_X + ix * GRID_CELL_SIZE_M, GRID_ORIGIN_Y + iy * GRID_CELL_SIZE_M
    return box(x, y, x + GRID_CELL_SIZE_M, y + GRID_CELL_SIZE_M)


def _project(geometry):
    from shapely import transform as shapely_transform

    return shapely_transform(
        geometry,
        lambda coords: np.column_stack(_TO_METRIC.transform(coords[:, 0], coords[:, 1])),
    )


def seoul_cells(boundaries_path: Path):
    """The same 200 m cells as ``services.precompute`` (land area > 1e-6 m^2)."""
    payload = json.loads(boundaries_path.read_text())
    raw_shapes = [shape(f["geometry"]) for f in payload["features"]]
    extent = grid.clip_to_seoul(tuple(unary_union(raw_shapes).bounds))
    boundaries = {f["properties"]["dong_code"]: _project(shape(f["geometry"])) for f in payload["features"]}
    boundary = unary_union(list(boundaries.values()))
    cells = []
    for ix, iy in grid.cells_in_bbox(extent):
        polygon = metric_cell(ix, iy)
        if polygon.intersection(boundary).area <= 1e-6:
            continue
        cells.append((grid.grid_id(ix, iy), ix, iy, polygon))
    return cells


def cell_centres(cells) -> np.ndarray:
    lnglat = np.array([grid.cell_center(ix, iy) for _, ix, iy, _ in cells])
    return project_points(lnglat[:, 0], lnglat[:, 1])


# ---------------------------------------------------------------- data


def load_pixels(path: Path) -> pd.DataFrame:
    """Usable VNP46A3 pixels with integer row/column indices from pixel_id."""
    frame = pd.read_csv(path)
    frame = frame[
        frame["radiance"].notna() & (frame["radiance"] >= 0) & frame["quality_flag"].isin([0, 1])
    ]
    parts = frame["pixel_id"].str.extract(r"^(?P<tile>h\d+v\d+)_(?P<row>\d+)_(?P<col>\d+)$")
    frame = frame.join(parts)
    frame["row"] = frame["row"].astype(int)
    frame["col"] = frame["col"].astype(int)
    return frame.reset_index(drop=True)


def full_blocks(pixels: pd.DataFrame) -> list[pd.DataFrame]:
    """Full 3x3 pixel blocks (~1.4 km) with all nine positions present."""
    row0, col0 = pixels["row"].min(), pixels["col"].min()
    pixels = pixels.assign(block_row=(pixels["row"] - row0) // 3, block_col=(pixels["col"] - col0) // 3)
    blocks = []
    for _, group in pixels.groupby(["block_row", "block_col"], sort=True):
        if len(group) == 9 and group["pixel_id"].is_unique:
            blocks.append(group.sort_values(["row", "col"]))
    return blocks


def night_lux(processed: Path) -> pd.DataFrame:
    from app.db.processed import load_processed
    from app.services.precompute import flagged_indicators

    data = load_processed(str(processed))
    sensors = {s.sensor_id: s for s in data.sensors}
    buckets: dict[str, list[float]] = {}
    for row in data.observations:
        if row.sensor_id not in sensors or row.illuminance_lux is None or row.illuminance_lux < 0:
            continue
        if "light" in flagged_indicators(row.quality_flag):
            continue
        if row.observed_at.astimezone(KST).hour not in NIGHT_HOURS:
            continue
        buckets.setdefault(row.sensor_id, []).append(float(row.illuminance_lux))
    rows = [
        {"sensor_id": sid, "lng": sensors[sid].lng, "lat": sensors[sid].lat,
         "lux_night_mean": mean(values), "samples": len(values)}
        for sid, values in buckets.items() if len(values) >= MIN_NIGHT_SAMPLES
    ]
    return pd.DataFrame(rows)


def overlay_value(polygon, pixel_tree, pixel_polygons, radiances) -> float | None:
    """Area-weighted radiance over a cell — the existing L0 definition."""
    parts = []
    for index in pixel_tree.query(polygon):
        overlap = polygon.intersection(pixel_polygons[int(index)]).area
        if overlap > 0:
            parts.append((overlap, radiances[int(index)]))
    if not parts:
        return None
    total = sum(area for area, _ in parts)
    return sum(area * radiance for area, radiance in parts) / total


# ---------------------------------------------------------------- model fit


@dataclass
class BlockData:
    values: np.ndarray
    radiance: np.ndarray

    @property
    def baseline(self) -> float:
        return float(self.radiance.mean())


def fit_beta(blocks: list[BlockData], alpha: float) -> np.ndarray:
    design = np.vstack([block.values - block.values.mean(axis=0, keepdims=True) for block in blocks])
    target = np.concatenate([np.log(block.radiance / block.baseline) for block in blocks])
    gram = design.T @ design + alpha * np.eye(design.shape[1])
    return np.linalg.solve(gram, design.T @ target)


def predict_block(block: BlockData, beta: np.ndarray) -> np.ndarray:
    centred = block.values - block.values.mean(axis=0, keepdims=True)
    weights = np.exp(centred @ beta)
    return block.baseline * weights / weights.mean()


def block_metrics(blocks: list[BlockData], beta: np.ndarray | None) -> dict:
    mae, rmse, spread, rhos = [], [], [], []
    for block in blocks:
        predicted = (np.full(len(block.radiance), block.baseline)
                     if beta is None else predict_block(block, beta))
        mae.append(float(np.abs(predicted - block.radiance).mean()))
        rmse.append(float(np.sqrt(((predicted - block.radiance) ** 2).mean())))
        if block.radiance.std() > 0 and predicted.std() > 0:
            spread.append(float(predicted.std() / block.radiance.std()))
            rho = spearman(predicted, block.radiance)
            if np.isfinite(rho):
                rhos.append(float(rho))
    return {
        "blocks": len(blocks),
        "blocks_with_variance": len(rhos),
        "mae": float(np.mean(mae)),
        "rmse": float(np.mean(rmse)),
        "within_block_spearman": float(np.mean(rhos)) if rhos else None,
        "variance_reproduction": float(np.mean(spread)) if spread else None,
    }


def fold_ids(blocks: list[pd.DataFrame]) -> np.ndarray:
    rows = np.array([int(block["block_row"].iloc[0]) for block in blocks])
    cols = np.array([int(block["block_col"].iloc[0]) for block in blocks])
    return (rows // SUPERBLOCK * 7 + cols // SUPERBLOCK) % FOLDS


def _mean_defined(values) -> float | None:
    defined = [float(value) for value in values if value is not None]
    return float(np.mean(defined)) if defined else None


def rankdata(values) -> np.ndarray:
    """Average ranks (1-based), tie-aware, without a scipy dependency."""
    array = np.asarray(values, dtype=float)
    order = np.argsort(array, kind="mergesort")
    ranks = np.empty(len(array), dtype=float)
    sorted_values = array[order]
    start = 0
    while start < len(array):
        end = start + 1
        while end < len(array) and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return ranks


def spearman(a, b) -> float:
    ra, rb = rankdata(a), rankdata(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denominator = float(np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))
    return float((ra * rb).sum() / denominator) if denominator > 0 else float("nan")


# ---------------------------------------------------------------- secondary check


def secondary_metrics(processed: Path, cells, exponents: dict[str, float]) -> dict:
    lux = night_lux(processed)
    if len(lux) < 8:
        return {"n_sensors": int(len(lux)), "note": "fewer than 8 sensors with >=12 night lux samples"}

    lights = pd.read_csv(processed / "nasa_night_lights.csv")
    records = [
        NightLightRecord(
            r.pixel_id, float(r.lng), float(r.lat), float(r.pixel_size_deg), str(r.product),
            pd.Timestamp(r.period_start).date(), pd.Timestamp(r.period_end).date(),
            float(r.radiance) if pd.notna(r.radiance) else None,
            int(r.quality_flag) if pd.notna(r.quality_flag) else None,
            int(r.cloud_flag) if pd.notna(r.cloud_flag) else None,
        )
        for r in lights.itertuples()
    ]
    usable = [record for record in records if is_usable(record.radiance, record.quality_flag)]
    layer = DominantPixelLayer(usable)
    pixel_polygons = layer.polygons
    pixel_tree = STRtree(pixel_polygons)
    radiances = [record.radiance for record in layer.records]

    cell_records = {gid: (ix, iy, polygon) for gid, ix, iy, polygon in cells}
    assignments = []
    for gid, _, _, polygon in cells:
        assignment = layer.assign(polygon)
        if assignment is None:
            continue
        assignments.append({
            "grid_id": gid,
            "pixel_id": assignment.record.pixel_id,
            "radiance": float(assignment.record.radiance),
            "weight": float(np.exp(exponents.get(gid, 0.0))),
            "overlap_area": assignment.overlap_area,
        })
    values = dasymetric_values(assignments)

    rows = []
    for sensor in lux.itertuples():
        gid = grid.grid_id(*grid.cell_of(sensor.lng, sensor.lat))
        if gid not in cell_records:
            continue
        l0 = overlay_value(cell_records[gid][2], pixel_tree, pixel_polygons, radiances)
        l1 = values.get(gid)
        if l0 is None or l1 is None:
            continue
        rows.append({"sensor_id": sensor.sensor_id, "lux": sensor.lux_night_mean, "l0": l0, "l1": l1})
    frame = pd.DataFrame(rows)
    if len(frame) < 8:
        return {"n_sensors": int(len(frame)), "note": "fewer than 8 sensors with lux and NASA overlap"}
    l0_rho = spearman(frame["lux"], frame["l0"])
    l1_rho = spearman(frame["lux"], frame["l1"])
    return {
        "n_sensors": int(len(frame)),
        "spearman_l0_lux": l0_rho,
        "spearman_l1_lux": l1_rho,
        "non_degraded": bool(l1_rho >= l0_rho - 1e-9),
    }


# ---------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stores", type=Path, required=True)
    parser.add_argument("--processed", type=Path, default=DEFAULT_PROCESSED)
    parser.add_argument("--roads", type=Path, default=DEFAULT_ROADS)
    parser.add_argument("--industrial", type=Path, default=DEFAULT_INDUSTRIAL)
    parser.add_argument("--out", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()

    print("loading spatial layers …")
    source = FeatureSource.load(args.stores, args.roads, args.industrial)
    pixels = load_pixels(args.processed / "nasa_night_lights.csv")
    blocks = full_blocks(pixels)
    print(f"usable pixels={len(pixels)} full 3x3 blocks={len(blocks)}")

    pixel_points = project_points(pixels["lng"].to_numpy(), pixels["lat"].to_numpy())
    transformed = transform_features(source.raw_features(pixel_points))
    mu = transformed.mean(axis=0)
    sigma = transformed.std(axis=0)
    sigma[sigma == 0] = 1.0
    standardised = pd.DataFrame((transformed - mu) / sigma, columns=list(FEATURE_NAMES), index=pixels.index)

    block_data = [
        BlockData(standardised.loc[block.index, list(FEATURE_NAMES)].to_numpy(dtype=float),
                  block["radiance"].to_numpy(dtype=float))
        for block in blocks
    ]
    folds = fold_ids(blocks)

    # --- spatial cross-validation and alpha selection
    cv = []
    for fold in range(FOLDS):
        train = [block for block, f in zip(block_data, folds) if f != fold]
        valid = [block for block, f in zip(block_data, folds) if f == fold]
        row = {"fold": fold, "train_blocks": len(train), "valid_blocks": len(valid),
               "l0": block_metrics(valid, None)}
        for alpha in ALPHAS:
            row[f"alpha_{alpha}"] = block_metrics(valid, fit_beta(train, alpha))
        cv.append(row)
        print(f"fold {fold}: L0 MAE {row['l0']['mae']:.2f} | "
              + " ".join(f"a{a}={row[f'alpha_{a}']['mae']:.2f}" for a in ALPHAS))

    mean_mae = {alpha: float(np.mean([row[f"alpha_{alpha}"]["mae"] for row in cv])) for alpha in ALPHAS}
    best_alpha = min(mean_mae, key=mean_mae.get)
    improved = sum(1 for row in cv if row[f"alpha_{best_alpha}"]["mae"] < row["l0"]["mae"])
    pooled_l0 = {
        key: float(np.mean([row["l0"][key] for row in cv]))
        for key in ("mae", "rmse")
    }
    pooled_l1 = {
        key: _mean_defined(row[f"alpha_{best_alpha}"][key] for row in cv)
        for key in ("mae", "rmse", "within_block_spearman", "variance_reproduction")
    }
    print(f"alpha={best_alpha} mean MAE {mean_mae[best_alpha]:.3f} "
          f"(L0 {pooled_l0['mae']:.3f}); folds improved {improved}/{FOLDS}")

    # --- final coefficients on all blocks
    beta = fit_beta(block_data, best_alpha)
    coefficients = {name: round(float(value), 6) for name, value in zip(FEATURE_NAMES, beta)}

    # --- 200 m cell exponents for the service
    print("enumerating Seoul 200 m cells …")
    cells = seoul_cells(args.processed / "admin_dong_boundaries.geojson")
    cell_standardised = (transform_features(source.raw_features(cell_centres(cells))) - mu) / sigma
    exponent = np.clip(cell_standardised @ beta, -3.0, 3.0)
    cell_exponent = {gid: round(float(value), 6) for (gid, _, _, _), value in zip(cells, exponent)}
    print(f"cells={len(cell_exponent)} exponent p5/p50/p95="
          f"{np.percentile(exponent, 5):.3f}/{np.percentile(exponent, 50):.3f}/{np.percentile(exponent, 95):.3f}")

    secondary = secondary_metrics(args.processed, cells, cell_exponent)
    print(f"secondary S-DoT: {secondary}")

    adopted = bool(improved >= 4 and secondary.get("non_degraded") is not False)
    artifact = {
        "artifact": "light_poi_model",
        "model_version": "light-poi-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": METHOD,
        "target": {
            "product": "VNP46A3",
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "unit": "nW/cm2/sr",
            "original_resolution_deg": 0.004167,
            "original_resolution_m": 463,
            "delivery_grid": "SEL200 200 m",
        },
        "model": {
            "form": "w = exp(sum_k beta_k z_k); value = pixel_radiance * w / area_weighted_mean(w) within the assigned pixel",
            "normalisation": "area-weighted pixel mean preserved over the cells assigned to each pixel",
            "standardisation": {
                name: {"mean": round(float(m), 6), "std": round(float(s), 6)}
                for name, m, s in zip(FEATURE_NAMES, mu, sigma)
            },
            "coefficients": coefficients,
            "alpha": best_alpha,
            "exponent_clip": [-3.0, 3.0],
            "dominant_pixel_rule": "cell assigned to the valid NASA pixel with the largest overlap area; pixel_id breaks ties",
            "confidence_factor": 0.9,
            "cell_count": len(cell_exponent),
        },
        "features": [
            {"name": spec.name, "definition": spec.definition,
             "transform": spec.transform, "source": spec.source}
            for spec in FEATURE_SPECS
        ],
        "validation": {
            "scale_transfer": {
                "description": "aggregate full 3x3 pixels to ~1.4 km, redistribute to 463 m, compare with observed 463 m",
                "folds": FOLDS,
                "spatial_fold_unit_pixels": SUPERBLOCK * 3,
                "alpha_cv_mean_mae": {str(alpha): round(value, 6) for alpha, value in mean_mae.items()},
                "folds_improved_vs_l0": improved,
                "per_fold": [
                    {"fold": row["fold"], "blocks": row["valid_blocks"],
                     "l0": row["l0"], "l1": row[f"alpha_{best_alpha}"]}
                    for row in cv
                ],
                "cv_l0": pooled_l0,
                "cv_l1": pooled_l1,
            },
            "secondary_sdot_lux": secondary,
            "adoption_criterion": "scale-transfer MAE improves versus L0 in >= 4/5 spatial folds and S-DoT rank correlation does not degrade",
            "adopted": adopted,
        },
        "provenance": {
            "stores": "소상공인시장진흥공단 상가(상권)정보 2026-09-30, 서울 552,422 points",
            "roads": "OpenStreetMap via Overpass (W4 cache), ODbL 1.0",
            "industrial": "OpenStreetMap landuse=industrial / man_made=works / building=industrial|warehouse, ODbL 1.0",
            "nasa": "NASA Black Marble VNP46A3 2026-09, Mandatory_Quality_Flag 0/1 only",
            "sdot": "서울 S-DoT 2026-10-12~18, night hours 22-01, light_* flags excluded, 0 lux kept",
        },
        "cell_exponent": cell_exponent,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, ensure_ascii=False, indent=1))
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB), adopted={adopted}")
    print(f"beta: {coefficients}")


if __name__ == "__main__":
    main()
