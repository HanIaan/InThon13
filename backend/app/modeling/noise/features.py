"""Spatial features from OSM road/rail lines at sensor locations.

Coordinates are projected to EPSG:5179 (metres). For each point the builder
computes the nearest distance per road class and the total line length of major /
minor roads within 50, 100 and 300 m. Rail is split into ground (``tunnel`` not
``yes``) and underground track. Distances are capped at ``cap_m`` when no line of
that class exists within the search radius, so the feature matrix has no missing
values; the cap is reported in the evaluation notes.

Buildings and green space are intentionally out of scope for W4.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

MAJOR_CLASSES = frozenset(
    {
        "motorway", "trunk", "primary", "secondary",
        "motorway_link", "trunk_link", "primary_link", "secondary_link",
    }
)
MINOR_CLASSES = frozenset(
    {"tertiary", "tertiary_link", "unclassified", "residential", "living_street"}
)
PRIMARY_CLASSES = frozenset({"primary", "primary_link"})
SECONDARY_CLASSES = frozenset({"secondary", "secondary_link"})
TERTIARY_CLASSES = frozenset({"tertiary", "tertiary_link"})
RESIDENTIAL_CLASSES = frozenset({"residential", "living_street", "unclassified"})

NEAREST_GROUPS = {
    "nearest_major_road_m": MAJOR_CLASSES,
    "nearest_primary_road_m": PRIMARY_CLASSES,
    "nearest_secondary_road_m": SECONDARY_CLASSES,
    "nearest_tertiary_road_m": TERTIARY_CLASSES,
    "nearest_residential_road_m": RESIDENTIAL_CLASSES,
}

RADII_M = (50, 100, 300)
DEFAULT_CAP_M = 2000.0
_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


@dataclass(frozen=True)
class LineRecord:
    geometry: LineString
    category: str
    way_class: str
    tunnel: bool


def project_lines(features: list[dict]) -> list[LineRecord]:
    """Project GeoJSON line features to EPSG:5179 metres."""
    records: list[LineRecord] = []
    for feature in features:
        properties = feature["properties"]
        coordinates = feature["geometry"]["coordinates"]
        if len(coordinates) < 2:
            continue
        xs, ys = _TO_METRIC.transform(
            [c[0] for c in coordinates], [c[1] for c in coordinates]
        )
        geometry = LineString(list(zip(xs, ys)))
        if not geometry.is_valid or geometry.length <= 0:
            continue
        records.append(
            LineRecord(
                geometry=geometry,
                category=properties["category"],
                way_class=properties["way_class"],
                tunnel=bool(properties.get("tunnel", False)),
            )
        )
    return records


def _empty_features() -> dict[str, float]:
    features = {name: DEFAULT_CAP_M for name in NEAREST_GROUPS}
    features["nearest_rail_ground_m"] = DEFAULT_CAP_M
    features["nearest_rail_underground_m"] = DEFAULT_CAP_M
    for radius in RADII_M:
        features[f"major_road_len_{radius}m"] = 0.0
        features[f"minor_road_len_{radius}m"] = 0.0
    features["rail_ground_len_300m"] = 0.0
    return features


def feature_names() -> list[str]:
    return list(_empty_features())


def compute_road_rail_features(
    points: pd.DataFrame,
    lines: list[LineRecord],
    *,
    cap_m: float = DEFAULT_CAP_M,
    project=None,
) -> pd.DataFrame:
    """One row per input point (columns sensor_id + feature names).

    ``points`` needs sensor_id/lng/lat. With no lines every distance equals
    ``cap_m`` and every length is 0 (honest "nothing nearby", not a guess).
    ``project`` overrides the WGS84 -> EPSG:5179 transform (used by unit tests).
    """
    if cap_m <= 0:
        raise ValueError("cap_m must be positive")
    project = project or _TO_METRIC.transform
    expected = feature_names()
    if not lines:
        rows = [_empty_features() for _ in range(len(points))]
        return pd.DataFrame(rows, columns=expected).assign(sensor_id=points["sensor_id"].to_numpy())

    geometries = [line.geometry for line in lines]
    tree = STRtree(geometries)
    major = np.array([line.category == "road" and line.way_class in MAJOR_CLASSES for line in lines])
    minor = np.array([line.category == "road" and line.way_class in MINOR_CLASSES for line in lines])
    rail_ground = np.array(
        [line.category == "rail" and not line.tunnel for line in lines]
    )
    rail_underground = np.array([line.category == "rail" and line.tunnel for line in lines])

    group_masks = {
        name: np.array([line.category == "road" and line.way_class in classes for line in lines])
        for name, classes in NEAREST_GROUPS.items()
    }

    xs, ys = project(points["lng"].to_numpy(), points["lat"].to_numpy())
    rows: list[dict[str, float]] = []
    for x, y in zip(xs, ys):
        point = Point(x, y)
        features = _empty_features()

        search = point.buffer(cap_m)
        candidates = [int(i) for i in tree.query(search)]
        if candidates:
            distances = np.array([point.distance(geometries[i]) for i in candidates])
            for name, mask in group_masks.items():
                selected = [k for k, i in enumerate(candidates) if mask[i]]
                if selected:
                    features[name] = float(min(distances[selected]))
            for name, mask in (("nearest_rail_ground_m", rail_ground),
                               ("nearest_rail_underground_m", rail_underground)):
                selected = [k for k, i in enumerate(candidates) if mask[i]]
                if selected:
                    features[name] = float(min(distances[selected]))

        for radius in RADII_M:
            circle = point.buffer(radius)
            for i in tree.query(circle):
                index = int(i)
                if not (major[index] or minor[index] or rail_ground[index]):
                    continue
                length = circle.intersection(geometries[index]).length
                if length <= 0:
                    continue
                if major[index]:
                    features[f"major_road_len_{radius}m"] += length
                if minor[index]:
                    features[f"minor_road_len_{radius}m"] += length
                if rail_ground[index] and radius == 300:
                    features["rail_ground_len_300m"] += length
        rows.append(features)

    result = pd.DataFrame(rows, columns=expected)
    result.insert(0, "sensor_id", points["sensor_id"].to_numpy())
    return result
