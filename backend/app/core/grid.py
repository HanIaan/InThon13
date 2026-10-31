"""200 m analysis grid. COORDINATOR-OWNED (grid_id format is part of the contract).

Cells are squares in EPSG:5179 anchored at (GRID_ORIGIN_X, GRID_ORIGIN_Y).
grid_id = "SEL200-<ix:04d>-<iy:04d>" where ix/iy are column/row indices from the origin.
In WGS84 the cells are very slightly non-rectangular; polygons are exact corner transforms.
"""
from __future__ import annotations

import math
from functools import lru_cache

from pyproj import Transformer

from app.contracts.constants import (
    GRID_CELL_SIZE_M,
    GRID_CRS,
    GRID_ID_PREFIX,
    GRID_ORIGIN_X,
    GRID_ORIGIN_Y,
    SEOUL_BBOX,
    WGS84,
)

_to_grid = Transformer.from_crs(WGS84, GRID_CRS, always_xy=True)
_to_wgs = Transformer.from_crs(GRID_CRS, WGS84, always_xy=True)

Cell = tuple[int, int]


def grid_id(ix: int, iy: int) -> str:
    return f"{GRID_ID_PREFIX}-{ix:04d}-{iy:04d}"


def parse_grid_id(value: str) -> Cell:
    prefix, ix, iy = value.split("-")
    if prefix != GRID_ID_PREFIX:
        raise ValueError(f"not a {GRID_ID_PREFIX} grid id: {value}")
    return int(ix), int(iy)


def cell_of(lng: float, lat: float) -> Cell:
    x, y = _to_grid.transform(lng, lat)
    return (
        math.floor((x - GRID_ORIGIN_X) / GRID_CELL_SIZE_M),
        math.floor((y - GRID_ORIGIN_Y) / GRID_CELL_SIZE_M),
    )


def _corner(ix: int, iy: int) -> tuple[float, float]:
    lng, lat = _to_wgs.transform(GRID_ORIGIN_X + ix * GRID_CELL_SIZE_M, GRID_ORIGIN_Y + iy * GRID_CELL_SIZE_M)
    return round(lng, 6), round(lat, 6)


@lru_cache(maxsize=65536)
def cell_polygon(ix: int, iy: int) -> list[list[tuple[float, float]]]:
    """GeoJSON Polygon coordinates (one closed CCW ring) in [lng, lat]."""
    ring = [_corner(ix, iy), _corner(ix + 1, iy), _corner(ix + 1, iy + 1), _corner(ix, iy + 1)]
    ring.append(ring[0])
    return [ring]


@lru_cache(maxsize=65536)
def cell_center(ix: int, iy: int) -> tuple[float, float]:
    lng, lat = _to_wgs.transform(
        GRID_ORIGIN_X + (ix + 0.5) * GRID_CELL_SIZE_M,
        GRID_ORIGIN_Y + (iy + 0.5) * GRID_CELL_SIZE_M,
    )
    return round(lng, 6), round(lat, 6)


def bbox_cell_range(bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """(ix_min, iy_min, ix_max, iy_max), inclusive, of cells intersecting the bbox."""
    min_lng, min_lat, max_lng, max_lat = bbox
    xs, ys = _to_grid.transform(
        [min_lng, max_lng, max_lng, min_lng], [min_lat, min_lat, max_lat, max_lat]
    )
    to_i = lambda v, o: math.floor((v - o) / GRID_CELL_SIZE_M)  # noqa: E731
    return (
        to_i(min(xs), GRID_ORIGIN_X), to_i(min(ys), GRID_ORIGIN_Y),
        to_i(max(xs), GRID_ORIGIN_X), to_i(max(ys), GRID_ORIGIN_Y),
    )


def bbox_cell_count(bbox: tuple[float, float, float, float]) -> int:
    x0, y0, x1, y1 = bbox_cell_range(bbox)
    return (x1 - x0 + 1) * (y1 - y0 + 1)


def clip_to_seoul(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    s = SEOUL_BBOX
    clipped = (max(bbox[0], s[0]), max(bbox[1], s[1]), min(bbox[2], s[2]), min(bbox[3], s[3]))
    if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
        return None
    return clipped


def cells_in_bbox(bbox: tuple[float, float, float, float]) -> list[Cell]:
    x0, y0, x1, y1 = bbox_cell_range(bbox)
    return [(ix, iy) for iy in range(y0, y1 + 1) for ix in range(x0, x1 + 1)]


def in_seoul_extent(lng: float, lat: float) -> bool:
    s = SEOUL_BBOX
    return s[0] <= lng <= s[2] and s[1] <= lat <= s[3]


def distance_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    x1, y1 = _to_grid.transform(lng1, lat1)
    x2, y2 = _to_grid.transform(lng2, lat2)
    return math.hypot(x2 - x1, y2 - y1)
