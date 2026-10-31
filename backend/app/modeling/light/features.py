"""Deterministic covariates for the light downscaling model.

All geometries are projected to EPSG:5179 (metres) before distance/length/area work.
The feature set is fixed by :data:`FEATURE_SPECS` and mirrored in the trained artifact;
changing it requires regenerating the artifact and bumping its version.

Sources
* 상가(상권)정보 — 소상공인시장진흥공단, 2026-09-30 snapshot, no usage restriction.
* OpenStreetMap — roads (W4 cache) and industrial land/buildings (fetched by
  :mod:`app.modeling.light.collect_osm_industrial`), ODbL 1.0.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from app.contracts.constants import SEOUL_BBOX
from app.modeling.light.model import project_geometry

_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)

STORE_RADIUS_SHORT_M = 100
STORE_RADIUS_M = 300
ROAD_RADIUS_M = 300
INDUSTRIAL_RADIUS_M = 300

#: Major road classes counted as street-light-carrier roads.
MAJOR_ROAD_CLASSES = frozenset({
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
})

#: Store categories treated as night-time activity (bars, karaoke, accommodation).
NIGHTLIFE_MID = frozenset({"주점", "일반 숙박", "기타 숙박"})
#: Store categories treated as late-night / 24-hour retail (convenience stores, 24h names).
CONVENIENCE_SUBSTR = "편의점"
LATE_NIGHT_NAME_TOKENS = ("24시", "24시간", "24h", "24H")


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    definition: str
    transform: str  # "log1p" | "identity"
    source: str

    def apply(self, raw: np.ndarray) -> np.ndarray:
        if self.transform == "log1p":
            return np.log1p(np.maximum(raw, 0.0))
        return np.asarray(raw, dtype=float)


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("stores_100", "반경 100m 상가 점포 수", "log1p", "stores"),
    FeatureSpec("stores_300", "반경 300m 상가 점포 수", "log1p", "stores"),
    FeatureSpec("nightlife_300", "반경 300m 주점·노래방·숙박 점포 수", "log1p", "stores"),
    FeatureSpec("late_night_300", "반경 300m 편의점·24시 상호 점포 수", "log1p", "stores"),
    FeatureSpec("industrial_frac_300", "반경 300m 공업용지(landuse=industrial) 면적 비율", "identity", "osm"),
    FeatureSpec("industrial_site_300", "반경 300m 공장·산업건물·창고 수", "log1p", "osm"),
    FeatureSpec("major_road_m_300", "반경 300m 주요도로(motorway~tertiary) 길이(m)", "log1p", "osm"),
)
FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SPECS)


def project_points(lng: np.ndarray, lat: np.ndarray) -> np.ndarray:
    x, y = _TO_METRIC.transform(np.asarray(lng, dtype=float), np.asarray(lat, dtype=float))
    return np.column_stack((x, y))


# ---------------------------------------------------------------- stores


def classify_stores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``nightlife`` / ``late_night`` boolean columns from category and name."""
    mid = frame["상권업종중분류명"].fillna("")
    sub = frame["상권업종소분류명"].fillna("")
    name = frame["상호명"].fillna("")
    nightlife = mid.isin(NIGHTLIFE_MID) | sub.str.contains("노래", na=False)
    late_night = (
        sub.str.contains(CONVENIENCE_SUBSTR, na=False)
        | name.str.contains("|".join(LATE_NIGHT_NAME_TOKENS), regex=True, na=False)
    )
    return frame.assign(nightlife=nightlife, late_night=late_night)


def load_stores(csv_path: str | Path, bbox: tuple[float, float, float, float] = SEOUL_BBOX) -> pd.DataFrame:
    """Load the store snapshot (utf-8-sig) and project valid in-bbox points."""
    columns = ["상호명", "상권업종중분류명", "상권업종소분류명", "경도", "위도"]
    frame = pd.read_csv(csv_path, usecols=columns, encoding="utf-8-sig", dtype=str)
    frame["경도"] = pd.to_numeric(frame["경도"], errors="coerce")
    frame["위도"] = pd.to_numeric(frame["위도"], errors="coerce")
    frame = frame.dropna(subset=["경도", "위도"])
    frame = frame[
        (frame["경도"] >= bbox[0]) & (frame["경도"] <= bbox[2])
        & (frame["위도"] >= bbox[1]) & (frame["위도"] <= bbox[3])
    ]
    frame = classify_stores(frame)
    frame["x"], frame["y"] = _TO_METRIC.transform(frame["경도"].to_numpy(), frame["위도"].to_numpy())
    return frame.reset_index(drop=True)


# ---------------------------------------------------------------- spatial layers


class StorePoints:
    """Neighbourhood point counts for all / nightlife / late-night stores."""

    def __init__(self, frame: pd.DataFrame):
        coordinates = frame[["x", "y"]].to_numpy(dtype=float)
        self._all = [Point(x, y) for x, y in coordinates]
        self.all = STRtree(self._all) if len(self._all) else None
        nightlife = coordinates[frame["nightlife"].to_numpy()]
        self._nightlife = [Point(x, y) for x, y in nightlife]
        self.nightlife = STRtree(self._nightlife) if self._nightlife else None
        late_night = coordinates[frame["late_night"].to_numpy()]
        self._late_night = [Point(x, y) for x, y in late_night]
        self.late_night = STRtree(self._late_night) if self._late_night else None

    @staticmethod
    def _count(tree, points: np.ndarray, radius: float) -> np.ndarray:
        if tree is None or len(points) == 0:
            return np.zeros(len(points))
        queries = [Point(x, y) for x, y in points]
        pairs = tree.query(queries, predicate="dwithin", distance=radius)
        return np.bincount(pairs[0], minlength=len(points)).astype(float)

    def counts(self, points: np.ndarray) -> dict[str, np.ndarray]:
        return {
            "stores_100": self._count(self.all, points, STORE_RADIUS_SHORT_M),
            "stores_300": self._count(self.all, points, STORE_RADIUS_M),
            "nightlife_300": self._count(self.nightlife, points, STORE_RADIUS_M),
            "late_night_300": self._count(self.late_night, points, STORE_RADIUS_M),
        }


class RoadNetwork:
    """Major-road length within a radius of each query point."""

    def __init__(self, geojson_path: str | Path, classes=MAJOR_ROAD_CLASSES):
        payload = json.loads(Path(geojson_path).read_text())
        self.lines = [
            project_geometry(shape(feature["geometry"]))
            for feature in payload["features"]
            if feature["properties"].get("way_class") in classes
        ]
        self.tree = STRtree(self.lines) if self.lines else None

    def length_within(self, points: np.ndarray, radius: float = ROAD_RADIUS_M) -> np.ndarray:
        if self.tree is None:
            return np.zeros(len(points))
        out = np.zeros(len(points))
        for row, (x, y) in enumerate(points):
            circle = Point(x, y).buffer(radius)
            total = 0.0
            for index in self.tree.query(circle):
                total += self.lines[int(index)].intersection(circle).length
            out[row] = total
        return out


class IndustrialLayer:
    """Industrial land area share and industrial-site counts within a radius."""

    def __init__(self, geojson_path: str | Path):
        payload = json.loads(Path(geojson_path).read_text())
        landuse, sites = [], []
        for feature in payload["features"]:
            geometry = project_geometry(shape(feature["geometry"]))
            if geometry.is_empty:
                continue
            category = feature["properties"].get("category")
            if category == "landuse_industrial":
                landuse.append(geometry)
            elif category in ("works", "industrial", "warehouse"):
                sites.append(geometry.centroid)
        merged = unary_union(landuse) if landuse else None
        self.landuse = list(merged.geoms) if merged is not None and merged.geom_type == "MultiPolygon" else (
            [merged] if merged is not None and not merged.is_empty else []
        )
        self.landuse_tree = STRtree(self.landuse) if self.landuse else None
        self._sites = [Point(point.x, point.y) for point in sites]
        self.site_tree = STRtree(self._sites) if self._sites else None

    def features(self, points: np.ndarray, radius: float = INDUSTRIAL_RADIUS_M) -> dict[str, np.ndarray]:
        fraction = np.zeros(len(points))
        circle_area = np.pi * radius * radius
        if self.landuse_tree is not None:
            for row, (x, y) in enumerate(points):
                circle = Point(x, y).buffer(radius)
                area = 0.0
                for index in self.landuse_tree.query(circle):
                    area += self.landuse[int(index)].intersection(circle).area
                fraction[row] = min(1.0, area / circle_area)
        if self.site_tree is None or len(points) == 0:
            sites = np.zeros(len(points))
        else:
            pairs = self.site_tree.query(
                [Point(x, y) for x, y in points], predicate="dwithin", distance=radius
            )
            sites = np.bincount(pairs[0], minlength=len(points)).astype(float)
        return {"industrial_frac_300": fraction, "industrial_site_300": sites}


@dataclass
class FeatureSource:
    """Raw (untransformed) feature values for arbitrary projected query points."""

    stores: StorePoints
    roads: RoadNetwork
    industrial: IndustrialLayer

    @classmethod
    def load(cls, stores_csv, roads_geojson, industrial_geojson) -> "FeatureSource":
        return cls(
            StorePoints(load_stores(stores_csv)),
            RoadNetwork(roads_geojson),
            IndustrialLayer(industrial_geojson),
        )

    def raw_features(self, points: np.ndarray) -> pd.DataFrame:
        points = np.asarray(points, dtype=float)
        values = self.stores.counts(points)
        values.update(self.industrial.features(points))
        values["major_road_m_300"] = self.roads.length_within(points)
        return pd.DataFrame({name: values[name] for name in FEATURE_NAMES})


def transform_features(raw: pd.DataFrame) -> np.ndarray:
    """Apply per-feature transforms (log1p) in :data:`FEATURE_SPECS` order."""
    return np.column_stack([spec.apply(raw[spec.name].to_numpy(dtype=float)) for spec in FEATURE_SPECS])
