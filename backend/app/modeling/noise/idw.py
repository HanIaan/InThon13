"""Reproduction of the production IDW noise interpolation (model A baseline).

``services/precompute.SensorField`` interpolates a cell's noise value as:

* if any sensor lies inside the cell: equal-weight mean of those sensors
  (``direct``);
* otherwise up to 8 sensors within ``max_distance_m`` of the cell **centroid**,
  weights ``1 / max(distance_m, 1) ** 2``, ties broken by ``sensor_id``;
* no sensor in range -> missing.

For cross-validation the target sensor is always in the held-out fold, and folds
are spatial blocks (>=1 km), so the ``direct`` branch cannot see another fold.
``NoiseIDW`` applies the same rules at an arbitrary prediction point (the sensor's
own location), which is the out-of-sample analogue of the production rule.
"""
from __future__ import annotations

import math

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree

from app.core import grid

MAX_DISTANCE_M = 1000.0
MAX_SENSORS = 8
_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


class NoiseIDW:
    """IDW over a set of (lng, lat, value, sensor_id) records."""

    def __init__(
        self,
        records: list[tuple[float, float, float, str]],
        *,
        max_distance_m: float = MAX_DISTANCE_M,
        k: int = MAX_SENSORS,
    ) -> None:
        if not math.isfinite(max_distance_m) or max_distance_m <= 0:
            raise ValueError("max_distance_m must be positive and finite")
        self.max_distance_m = float(max_distance_m)
        self.k = int(k)
        if not records:
            self.records = []
            self.points: list[Point] = []
            self.tree = None
            self.by_cell: dict[tuple[int, int], list[int]] = {}
            return
        self.records = records
        self.points = [Point(_TO_METRIC.transform(lng, lat)) for lng, lat, _, _ in records]
        self.tree = STRtree(self.points)
        self.by_cell = {}
        for i, (lng, lat, _, _) in enumerate(records):
            self.by_cell.setdefault(grid.cell_of(lng, lat), []).append(i)

    def __len__(self) -> int:
        return len(self.records)

    def predict(self, lng: float, lat: float) -> float | None:
        """Interpolated dB at (lng, lat), or None when no sensor is in range."""
        if not self.records:
            return None
        point = Point(_TO_METRIC.transform(lng, lat))
        cell = grid.cell_of(lng, lat)
        indices = self.by_cell.get(cell, [])
        direct = bool(indices)
        if not direct:
            candidates = (
                int(i)
                for i in self.tree.query(point.buffer(self.max_distance_m))
                if point.distance(self.points[i]) <= self.max_distance_m
            )
            indices = sorted(
                candidates,
                key=lambda i: (point.distance(self.points[i]), self.records[i][3]),
            )[: self.k]
        if not indices:
            return None
        distances = [point.distance(self.points[i]) for i in indices]
        weights = [1.0 if direct else 1.0 / max(d, 1.0) ** 2 for d in distances]
        values = [self.records[i][2] for i in indices]
        return float(np.average(values, weights=weights))


def idw_predictions(
    train: "pd.DataFrame",  # noqa: F821 - pandas imported lazily below
    query: "pd.DataFrame",  # noqa: F821
    *,
    value_column: str = "noise_db",
    slot_column: str = "slot",
    max_distance_m: float = MAX_DISTANCE_M,
) -> np.ndarray:
    """IDW predictions for query rows, one field per time slot built from ``train``.

    Both frames need sensor_id/lng/lat/slot plus ``value_column``. Returns NaN
    where no training sensor is within ``max_distance_m``.
    """
    import pandas as pd

    slots = pd.unique(query[slot_column])
    predictions = np.full(len(query), np.nan, dtype=float)
    for slot in slots:
        train_slot = train[train[slot_column] == slot]
        field = NoiseIDW(
            [
                (float(r.lng), float(r.lat), float(getattr(r, value_column)), str(r.sensor_id))
                for r in train_slot.itertuples(index=False)
            ],
            max_distance_m=max_distance_m,
        )
        mask = (query[slot_column] == slot).to_numpy()
        predictions[mask] = [
            field.predict(float(lng), float(lat)) if field else None
            for lng, lat in zip(query.loc[mask, "lng"], query.loc[mask, "lat"])
        ]
    return predictions
