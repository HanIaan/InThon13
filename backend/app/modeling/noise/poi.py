"""Commercial-store (상가업소) proximity features around sensor and grid points.

Source: 소상공인시장진흥공단 상가(상권)정보, Seoul snapshot ``stores_seoul_202609.csv``
(basis date 2026-09-30, no usage restrictions). One row per store with a WGS84 point.
The file has no factory category, so factories come from OpenStreetMap instead
(see ``industry.py``).

Categories are the exact 대/중/소분류 names of the file, not fuzzy matching:

* ``all``      every store
* ``food``     상권업종대분류명 == '음식'
* ``pub``      상권업종중분류명 == '주점'
               (요리 주점 / 생맥주 전문 / 일반 유흥 주점 / 무도 유흥 주점)
* ``karaoke``  상권업종소분류명 == '노래방'
* ``pc_bang``  상권업종소분류명 == 'PC방'
* ``lodging``  상권업종대분류명 == '숙박'
* ``retail``   상권업종대분류명 == '소매'

Counts are exact point-in-circle counts in EPSG:5179 metres at 50/100/300 m.
Nearest distances are capped at ``DEFAULT_CAP_M`` when no store of that category
exists within it; zero is a real "on top of the point" value, never a missing guess.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree

POI_RADII_M = (50, 100, 300)
DEFAULT_CAP_M = 2000.0
NEAREST_CATEGORIES = ("pub", "karaoke")

STORE_COLUMNS = [
    "상권업종대분류명",
    "상권업종중분류명",
    "상권업종소분류명",
    "경도",
    "위도",
]

# category -> (column, exact value); None value means "every store".
CATEGORIES: dict[str, tuple[str, str] | None] = {
    "all": None,
    "food": ("상권업종대분류명", "음식"),
    "pub": ("상권업종중분류명", "주점"),
    "karaoke": ("상권업종소분류명", "노래방"),
    "pc_bang": ("상권업종소분류명", "PC방"),
    "lodging": ("상권업종대분류명", "숙박"),
    "retail": ("상권업종대분류명", "소매"),
}

_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


def feature_names() -> list[str]:
    """Ordered feature names produced by :func:`compute_poi_features`."""
    names = [f"poi_{category}_{radius}m" for category in CATEGORIES for radius in POI_RADII_M]
    names.extend(f"nearest_{category}_m" for category in NEAREST_CATEGORIES)
    return names


def load_stores(csv_path: str | Path) -> pd.DataFrame:
    """Read only the columns needed for POI features.

    Returns columns ``lng``, ``lat`` and one boolean column per category.
    Rows without finite WGS84 coordinates are dropped (they cannot be placed).
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"store file not found: {path}")
    frame = pd.read_csv(path, usecols=STORE_COLUMNS, encoding="utf-8-sig")
    lng = pd.to_numeric(frame["경도"], errors="coerce")
    lat = pd.to_numeric(frame["위도"], errors="coerce")
    keep = np.isfinite(lng) & np.isfinite(lat)
    frame = frame.loc[keep].reset_index(drop=True)
    out = pd.DataFrame({"lng": lng[keep].to_numpy(dtype=float),
                        "lat": lat[keep].to_numpy(dtype=float)})
    for category, rule in CATEGORIES.items():
        if rule is None:
            out[category] = True
            continue
        column, value = rule
        out[category] = (frame[column].fillna("") == value).to_numpy()
    return out


class StoreField:
    """Spatial index of the store points with per-category membership masks."""

    def __init__(self, stores: pd.DataFrame, cap_m: float = DEFAULT_CAP_M) -> None:
        if not np.isfinite(cap_m) or cap_m <= 0:
            raise ValueError("cap_m must be positive and finite")
        self.cap_m = float(cap_m)
        self.masks = {name: stores[name].to_numpy(dtype=bool) for name in CATEGORIES}
        xs, ys = _TO_METRIC.transform(stores["lng"].to_numpy(), stores["lat"].to_numpy())
        self.points = [Point(x, y) for x, y in zip(xs, ys)]
        self.tree = STRtree(self.points) if self.points else None
        # Per-category indexes only where a nearest distance is required.
        self.category_points: dict[str, list[Point]] = {}
        self.category_trees: dict[str, STRtree | None] = {}
        for category in NEAREST_CATEGORIES:
            members = [p for p, inside in zip(self.points, self.masks[category]) if inside]
            self.category_points[category] = members
            self.category_trees[category] = STRtree(members) if members else None

    def __len__(self) -> int:
        return len(self.points)

    def features(self, lng: float, lat: float) -> dict[str, float]:
        """POI features at one WGS84 point."""
        x, y = _TO_METRIC.transform(lng, lat)
        point = Point(x, y)
        out = {f"poi_{c}_{r}m": 0.0 for c in CATEGORIES for r in POI_RADII_M}
        for c in NEAREST_CATEGORIES:
            out[f"nearest_{c}_m"] = self.cap_m
        if self.tree is None:
            return out
        reach = max(POI_RADII_M) + 1.0
        indices = [int(i) for i in self.tree.query(point.buffer(reach))]
        if indices:
            distances = np.array([point.distance(self.points[i]) for i in indices])
            for category, mask in self.masks.items():
                selected = distances[mask[indices]]
                for radius in POI_RADII_M:
                    out[f"poi_{category}_{radius}m"] = float((selected <= radius).sum())
        for category in NEAREST_CATEGORIES:
            tree = self.category_trees[category]
            if tree is None:
                continue
            nearest = self.category_points[category][int(tree.nearest(point))]
            out[f"nearest_{category}_m"] = float(min(point.distance(nearest), self.cap_m))
        return out


def compute_poi_features(
    points: pd.DataFrame,
    stores: pd.DataFrame,
    *,
    cap_m: float = DEFAULT_CAP_M,
    id_column: str = "sensor_id",
) -> pd.DataFrame:
    """One row per point: ``id_column`` plus :func:`feature_names`.

    ``points`` needs ``id_column``/``lng``/``lat``. With no stores every count is
    0 and every nearest distance is ``cap_m`` (honest "nothing nearby").
    """
    field = StoreField(stores, cap_m=cap_m)
    rows = [field.features(float(lng), float(lat)) for lng, lat in zip(points["lng"], points["lat"])]
    result = pd.DataFrame(rows, columns=feature_names())
    result.insert(0, id_column, points[id_column].to_numpy())
    return result
