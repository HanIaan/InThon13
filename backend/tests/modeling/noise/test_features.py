"""Spatial feature tests in EPSG:5179 metres (offline)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from app.modeling.noise import features as spatial


def _line(coordinates, category="road", way_class="primary", tunnel=False):
    return spatial.LineRecord(LineString(coordinates), category, way_class, tunnel)


def _points(*coords):
    return pd.DataFrame(
        [{"sensor_id": f"P{i}", "lng": lng, "lat": lat} for i, (lng, lat) in enumerate(coords)]
    )


def _features_at(lines, point_metric):
    points = _points((0.0, 0.0))
    x, y = point_metric

    def project(lng, lat):
        return np.full(len(lng), x, dtype=float), np.full(len(lat), y, dtype=float)

    return spatial.compute_road_rail_features(points, lines, project=project).iloc[0]


def test_road_distance_and_lengths_in_metres():
    road = _line([(0.0, 0.0), (1000.0, 0.0)], way_class="primary")
    row = _features_at([road], (500.0, 30.0))
    assert row["nearest_major_road_m"] == pytest.approx(30.0, abs=1e-6)
    assert row["nearest_primary_road_m"] == pytest.approx(30.0, abs=1e-6)
    # chord of a circle of radius 50 at 30 m offset: 2*sqrt(50^2-30^2); shapely
    # buffers are polygons, so allow 1% for the arc approximation.
    assert row["major_road_len_50m"] == pytest.approx(80.0, rel=0.01)
    assert row["major_road_len_100m"] == pytest.approx(2 * (100**2 - 30**2) ** 0.5, rel=0.01)
    assert row["minor_road_len_100m"] == 0.0


def test_class_groups_split_major_minor():
    major = _line([(-100.0, 200.0), (100.0, 200.0)], way_class="secondary")
    minor = _line([(-100.0, 40.0), (100.0, 40.0)], way_class="residential")
    row = _features_at([major, minor], (0.0, 0.0))
    assert row["nearest_major_road_m"] == pytest.approx(200.0)
    assert row["nearest_residential_road_m"] == pytest.approx(40.0)
    assert row["major_road_len_50m"] == 0.0
    assert row["minor_road_len_50m"] > 0.0


def test_underground_rail_not_counted_as_ground():
    underground = _line([(-500.0, 20.0), (500.0, 20.0)], "rail", "subway", tunnel=True)
    row = _features_at([underground], (0.0, 0.0))
    assert row["nearest_rail_ground_m"] == spatial.DEFAULT_CAP_M
    assert row["nearest_rail_underground_m"] == pytest.approx(20.0)
    assert row["rail_ground_len_300m"] == 0.0

    ground = _line([(-500.0, 20.0), (500.0, 20.0)], "rail", "rail", tunnel=False)
    row = _features_at([ground], (0.0, 0.0))
    assert row["nearest_rail_ground_m"] == pytest.approx(20.0)
    assert row["rail_ground_len_300m"] > 0.0


def test_no_lines_returns_cap_not_a_guess():
    row = _features_at([], (0.0, 0.0))
    assert row["nearest_major_road_m"] == spatial.DEFAULT_CAP_M
    assert row["major_road_len_300m"] == 0.0
    assert row["nearest_rail_ground_m"] == spatial.DEFAULT_CAP_M


def test_feature_names_are_stable():
    names = spatial.feature_names()
    assert "nearest_major_road_m" in names
    assert "major_road_len_300m" in names
    assert "minor_road_len_50m" in names
    assert "rail_ground_len_300m" in names
    assert len(names) == len(set(names))
