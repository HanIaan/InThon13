"""Inference side of the L1 dasymetric light model.

The model redistributes each NASA VNP46A3 monthly pixel (~463 m) over the 200 m cells
assigned to it while preserving the area-weighted pixel mean::

    w_cell   = exp(cell_exponent[grid_id])          # from the versioned artifact
    mean_w   = sum(area * w) / sum(area)            # over cells assigned to the pixel
    value    = pixel_radiance * w / mean_w

``cell_exponent`` is precomputed for every Seoul 200 m cell and stored in the artifact,
so this module reads only ``artifacts/light_poi_model_v1.json`` at request/precompute
time. No raw POI, OSM or store file is read here.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from pyproj import Transformer
from shapely import transform as shapely_transform
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from app.contracts.constants import GRID_CRS, WGS84

_TO_METRIC = Transformer.from_crs(WGS84, GRID_CRS, always_xy=True)


def project_geometry(geometry):
    """Project a WGS84 shapely geometry to EPSG:5179 (service-safe: no pandas/scipy)."""
    return shapely_transform(
        geometry,
        lambda coords: np.column_stack(_TO_METRIC.transform(coords[:, 0], coords[:, 1])),
    )

MODEL_ENV = "LIGHT_MODEL"
ARTIFACT_ENV = "LIGHT_POI_ARTIFACT"
MODEL_NAME = "poi"
METHOD = "pixel_overlay_poi"
NOTE = "월간 위성값을 상가·공장 분포로 200m에 재분배한 추정; 저녁·밤·새벽 동일 값이며 관측 시각은 미제공."
DEFAULT_ARTIFACT = Path(__file__).parent / "artifacts" / "light_poi_model_v1.json"


def light_model_name() -> str:
    """``LIGHT_MODEL`` values: ``overlay`` (default, existing L0) or ``poi`` (L1)."""
    return os.environ.get(MODEL_ENV, "overlay").strip().lower()


def is_usable(radiance, quality_flag) -> bool:
    return radiance is not None and radiance >= 0 and quality_flag in (0, 1)


@dataclass(frozen=True)
class PixelAssignment:
    record: object          # NightLightRecord-like: pixel_id, radiance, quality_flag, period_*
    overlap_area: float     # active cell area inside the chosen pixel (EPSG:5179 m^2)
    covered_fraction: float # union of all valid pixels / cell area


class DominantPixelLayer:
    """Assign each cell to the valid NASA pixel covering most of it."""

    def __init__(self, records: Iterable):
        selected = {}
        for record in sorted(
            (r for r in records if is_usable(r.radiance, r.quality_flag)),
            key=lambda r: (r.period_end, -(r.period_end - r.period_start).days, r.product, r.pixel_id),
        ):
            selected[record.pixel_id] = record
        self.records = list(selected.values())
        self.polygons = [
            project_geometry(box(
                r.lng - r.pixel_size_deg / 2, r.lat - r.pixel_size_deg / 2,
                r.lng + r.pixel_size_deg / 2, r.lat + r.pixel_size_deg / 2,
            ))
            for r in self.records
        ]
        self.tree = STRtree(self.polygons) if self.polygons else None

    def assign(self, cell_polygon) -> PixelAssignment | None:
        if self.tree is None:
            return None
        candidates = []
        for index in self.tree.query(cell_polygon):
            overlap = cell_polygon.intersection(self.polygons[int(index)])
            if overlap.area > 0:
                candidates.append((overlap.area, self.records[int(index)].pixel_id, int(index), overlap))
        if not candidates:
            return None
        # Largest overlap wins; the smallest pixel_id breaks exact ties.
        best_area = max(area for area, _, _, _ in candidates)
        area, _, index, _ = min(
            (candidate for candidate in candidates if candidate[0] == best_area),
            key=lambda candidate: candidate[1],
        )
        covered = unary_union([overlap for _, _, _, overlap in candidates]).area / cell_polygon.area
        return PixelAssignment(self.records[index], float(area), float(min(1.0, covered)))


def dasymetric_values(assignments: Iterable[dict]) -> dict[str, float]:
    """Preserve the area-weighted pixel mean while spreading radiance inside each pixel.

    ``assignments`` rows need ``grid_id``, ``pixel_id``, ``radiance``, ``weight`` and
    ``overlap_area``. Returns ``grid_id -> estimated radiance``.
    """
    groups: dict[str, list[dict]] = {}
    for row in assignments:
        groups.setdefault(row["pixel_id"], []).append(row)
    values: dict[str, float] = {}
    for rows in groups.values():
        total_area = sum(row["overlap_area"] for row in rows)
        if total_area <= 0:
            continue
        mean_weight = sum(row["overlap_area"] * row["weight"] for row in rows) / total_area
        if not math.isfinite(mean_weight) or mean_weight <= 0:
            mean_weight = 1.0
        for row in rows:
            values[row["grid_id"]] = row["radiance"] * row["weight"] / mean_weight
    return values


@dataclass(frozen=True)
class LightPoiModel:
    model_version: str
    method: str
    confidence_factor: float
    cell_exponent: dict[str, float]
    coefficients: dict[str, float]
    features: list[dict]
    validation: dict
    path: str

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LightPoiModel":
        artifact_path = Path(path or os.environ.get(ARTIFACT_ENV) or DEFAULT_ARTIFACT)
        payload = json.loads(artifact_path.read_text())
        if payload.get("method") != METHOD:
            raise ValueError(f"unexpected light model method: {payload.get('method')!r}")
        return cls(
            model_version=payload["model_version"],
            method=payload["method"],
            confidence_factor=float(payload["model"].get("confidence_factor", 1.0)),
            cell_exponent={key: float(value) for key, value in payload["cell_exponent"].items()},
            coefficients={key: float(value) for key, value in payload["model"]["coefficients"].items()},
            features=list(payload.get("features", [])),
            validation=payload.get("validation", {}),
            path=str(artifact_path),
        )

    def exponent(self, grid_id: str) -> float:
        return self.cell_exponent.get(grid_id, 0.0)

    def weight(self, grid_id: str) -> float:
        return math.exp(self.exponent(grid_id))
