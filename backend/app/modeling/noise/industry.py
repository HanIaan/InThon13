"""Factory / industrial-land features from cached OSM polygons.

The commercial-store file has no factory category, so industrial sources come from
OpenStreetMap (``landuse=industrial``, ``man_made=works``,
``building=industrial|warehouse``; see ``osm.fetch_industrial``). Polygons are
unioned first so a warehouse drawn inside an industrial estate is not counted
twice, then each point gets:

* ``industrial_area_ratio_100m`` / ``_300m`` — unioned industrial footprint area
  intersected with the circle, divided by the circle area (0..1);
* ``nearest_industrial_m`` — distance to the nearest footprint (0 inside one),
  capped at ``DEFAULT_CAP_M`` when nothing lies within it.

All geometry is in EPSG:5179 metres. With no polygons every ratio is 0 and every
distance is the cap — "nothing nearby", not an imputed value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import get_parts, transform as shapely_transform, union_all
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

INDUSTRIAL_RADII_M = (100, 300)
DEFAULT_CAP_M = 2000.0
_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


def feature_names() -> list[str]:
    return [f"industrial_area_ratio_{radius}m" for radius in INDUSTRIAL_RADII_M] + [
        "nearest_industrial_m"
    ]


def _project_geometry(geometry: BaseGeometry) -> BaseGeometry:
    def project(coords: np.ndarray) -> np.ndarray:
        x, y = _TO_METRIC.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely_transform(geometry, project)


def project_polygons(features: list[dict]) -> list[BaseGeometry]:
    """Project GeoJSON polygon features to EPSG:5179; invalid/empty shapes dropped."""
    polygons = []
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            continue
        geometry = _project_geometry(geometry)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if geometry.is_empty or geometry.area <= 0:
            continue
        polygons.append(geometry)
    return polygons


class IndustrialField:
    """Unioned industrial footprint with a spatial index over its parts."""

    def __init__(self, polygons: list[BaseGeometry], cap_m: float = DEFAULT_CAP_M) -> None:
        if not np.isfinite(cap_m) or cap_m <= 0:
            raise ValueError("cap_m must be positive and finite")
        self.cap_m = float(cap_m)
        self.parts: list[BaseGeometry] = []
        if polygons:
            merged = union_all(polygons)
            self.parts = [p for p in get_parts(merged) if not p.is_empty and p.area > 0]
        self.tree = STRtree(self.parts) if self.parts else None

    def __len__(self) -> int:
        return len(self.parts)

    def features(self, lng: float, lat: float) -> dict[str, float]:
        x, y = _TO_METRIC.transform(lng, lat)
        point = Point(x, y)
        out = {f"industrial_area_ratio_{radius}m": 0.0 for radius in INDUSTRIAL_RADII_M}
        out["nearest_industrial_m"] = self.cap_m
        if self.tree is None:
            return out
        nearest_index = int(self.tree.nearest(point))
        out["nearest_industrial_m"] = float(min(point.distance(self.parts[nearest_index]), self.cap_m))
        reach = max(INDUSTRIAL_RADII_M) + 1.0
        candidates = [int(i) for i in self.tree.query(point.buffer(reach))]
        if not candidates:
            return out
        for radius in INDUSTRIAL_RADII_M:
            circle = point.buffer(radius)
            covered = sum(self.parts[i].intersection(circle).area for i in candidates)
            out[f"industrial_area_ratio_{radius}m"] = float(min(covered / circle.area, 1.0))
        return out


def compute_industrial_features(
    points: pd.DataFrame,
    polygons: list[BaseGeometry],
    *,
    cap_m: float = DEFAULT_CAP_M,
    id_column: str = "sensor_id",
) -> pd.DataFrame:
    """One row per point: ``id_column`` plus :func:`feature_names`."""
    field = IndustrialField(polygons, cap_m=cap_m)
    rows = [field.features(float(lng), float(lat)) for lng, lat in zip(points["lng"], points["lat"])]
    result = pd.DataFrame(rows, columns=feature_names())
    result.insert(0, id_column, points[id_column].to_numpy())
    return result
