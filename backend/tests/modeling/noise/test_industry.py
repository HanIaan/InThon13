"""Industrial-land (OSM) feature tests — area ratios, nearest distance, caps."""
from __future__ import annotations

import pandas as pd
import pytest
from pyproj import Transformer

from app.modeling.noise import industry

_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)
_TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)

ORIGIN_LNG, ORIGIN_LAT = 127.0, 37.5


def _metric_square(east0: float, east1: float, north0: float, north1: float) -> dict:
    """A WGS84 square feature given its EPSG:5179 bounds relative to the origin."""
    x0, y0 = _TO_METRIC.transform(ORIGIN_LNG, ORIGIN_LAT)
    corners = [
        (x0 + east0, y0 + north0),
        (x0 + east1, y0 + north0),
        (x0 + east1, y0 + north1),
        (x0 + east0, y0 + north1),
        (x0 + east0, y0 + north0),
    ]
    lngs, lats = _TO_WGS.transform([c[0] for c in corners], [c[1] for c in corners])
    return {
        "type": "Feature",
        "properties": {"kind": "landuse"},
        "geometry": {"type": "Polygon", "coordinates": [list(zip(lngs, lats))]},
    }


def test_feature_names():
    assert industry.feature_names() == [
        "industrial_area_ratio_100m",
        "industrial_area_ratio_300m",
        "nearest_industrial_m",
    ]


def test_area_ratio_and_zero_distance_inside_footprint():
    polygons = industry.project_polygons([_metric_square(-50, 50, -50, 50)])
    field = industry.IndustrialField(polygons)
    features = field.features(ORIGIN_LNG, ORIGIN_LAT)
    assert features["nearest_industrial_m"] == 0.0
    # WGS84 <-> EPSG:5179 round-trip distorts the square by <0.2 %, hence rel=0.005.
    assert features["industrial_area_ratio_100m"] == pytest.approx(
        10_000 / (3.141592653589793 * 100**2), rel=0.005
    )
    assert features["industrial_area_ratio_300m"] == pytest.approx(
        10_000 / (3.141592653589793 * 300**2), rel=0.005
    )


def test_ratio_is_clipped_to_one():
    polygons = industry.project_polygons([_metric_square(-400, 400, -400, 400)])
    features = industry.IndustrialField(polygons).features(ORIGIN_LNG, ORIGIN_LAT)
    assert features["industrial_area_ratio_100m"] == pytest.approx(1.0)
    assert features["industrial_area_ratio_300m"] == pytest.approx(1.0)


def test_nearest_distance_and_cap_when_far():
    polygons = industry.project_polygons([_metric_square(500, 600, -50, 50)])
    near = industry.IndustrialField(polygons, cap_m=2000).features(ORIGIN_LNG, ORIGIN_LAT)
    assert near["nearest_industrial_m"] == pytest.approx(500, abs=0.5)
    assert near["industrial_area_ratio_300m"] == 0.0
    capped = industry.IndustrialField(polygons, cap_m=300).features(ORIGIN_LNG, ORIGIN_LAT)
    assert capped["nearest_industrial_m"] == 300.0


def test_overlapping_polygons_are_unioned_not_double_counted():
    square = _metric_square(-100, 100, -100, 100)
    one = industry.IndustrialField(industry.project_polygons([square]))
    two = industry.IndustrialField(industry.project_polygons([square, square]))
    assert len(one) == len(two) == 1  # merged into one part
    assert one.features(ORIGIN_LNG, ORIGIN_LAT) == two.features(ORIGIN_LNG, ORIGIN_LAT)


def test_empty_polygons_are_honest_zeros_and_cap():
    points = pd.DataFrame([{"sensor_id": "p0", "lng": ORIGIN_LNG, "lat": ORIGIN_LAT}])
    result = industry.compute_industrial_features(points, [], cap_m=777)
    assert list(result.columns) == ["sensor_id", *industry.feature_names()]
    assert result.loc[0, "industrial_area_ratio_100m"] == 0
    assert result.loc[0, "industrial_area_ratio_300m"] == 0
    assert result.loc[0, "nearest_industrial_m"] == 777


def test_negative_cap_rejected():
    with pytest.raises(ValueError):
        industry.IndustrialField([], cap_m=-1)


def test_degenerate_polygon_is_dropped():
    degenerate = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[127.0, 37.5], [127.0, 37.5], [127.0, 37.5], [127.0, 37.5]]]},
    }
    assert industry.project_polygons([degenerate]) == []
