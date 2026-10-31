"""Feature building for the light downscaling model (no network)."""
import json

import numpy as np
import pandas as pd
import pytest

from app.modeling.light.features import (
    FEATURE_NAMES,
    FeatureSource,
    IndustrialLayer,
    RoadNetwork,
    StorePoints,
    classify_stores,
    project_points,
    transform_features,
)


def _stores_frame(rows):
    frame = pd.DataFrame(rows, columns=["상호명", "상권업종중분류명", "상권업종소분류명", "x", "y"])
    return classify_stores(frame)


def test_classify_stores_flags_nightlife_and_late_night():
    frame = _stores_frame([
        ("호프집", "주점", "생맥주 전문", 0, 0),
        ("노래연습장", "유원지·오락", "노래방", 0, 0),
        ("호텔", "일반 숙박", "호텔", 0, 0),
        ("편의점", "종합 소매", "편의점", 0, 0),
        ("24시 마트", "종합 소매", "기타", 0, 0),
        ("일반 식당", "한식", "백반", 0, 0),
    ])
    assert frame["nightlife"].tolist() == [True, True, True, False, False, False]
    assert frame["late_night"].tolist() == [False, False, False, True, True, False]


def test_store_counts_use_radius_and_flags():
    frame = _stores_frame([
        ("a", "주점", "요리 주점", 0, 0),
        ("b", "한식", "백반", 50, 0),
        ("c", "한식", "백반", 250, 0),
        ("d", "한식", "백반", 500, 0),
    ])
    points = StorePoints(frame).counts(np.array([[0.0, 0.0]]))
    assert points["stores_100"] == [2]
    assert points["stores_300"] == [3]
    assert points["nightlife_300"] == [1]
    assert points["late_night_300"] == [0]


def test_transform_features_applies_log1p_in_spec_order():
    raw = pd.DataFrame([dict.fromkeys(FEATURE_NAMES, 0.0)])
    raw.loc[0, "stores_300"] = 9.0
    transformed = transform_features(raw)
    assert list(transformed[0]) == pytest.approx([0, np.log1p(9.0), 0, 0, 0, 0, 0])


def _write_geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def test_road_length_within_radius(tmp_path):
    # ~0.001 deg lon at Seoul latitude is ~88 m.
    path = tmp_path / "roads.geojson"
    _write_geojson(path, [{
        "type": "Feature",
        "properties": {"way_class": "primary"},
        "geometry": {"type": "LineString", "coordinates": [[126.978, 37.5665], [126.979, 37.5665]]},
    }, {
        "type": "Feature",
        "properties": {"way_class": "residential"},
        "geometry": {"type": "LineString", "coordinates": [[126.978, 37.5665], [126.979, 37.5665]]},
    }])
    network = RoadNetwork(path)
    point = project_points(np.array([126.9785]), np.array([37.5665]))
    length = network.length_within(point, radius=300)[0]
    assert 60 < length < 110


def test_industrial_fraction_and_site_count(tmp_path):
    path = tmp_path / "industrial.geojson"
    # Square ~0.001 x 0.001 deg centred on the query point.
    square = {"type": "Polygon", "coordinates": [[
        [126.9775, 37.5660], [126.9785, 37.5660], [126.9785, 37.5668],
        [126.9775, 37.5668], [126.9775, 37.5660],
    ]]}
    _write_geojson(path, [
        {"type": "Feature", "properties": {"category": "landuse_industrial"}, "geometry": square},
        {"type": "Feature", "properties": {"category": "works"}, "geometry": square},
    ])
    layer = IndustrialLayer(path)
    point = project_points(np.array([126.978]), np.array([37.5664]))
    values = layer.features(point, radius=300)
    assert 0 < values["industrial_frac_300"][0] < 1
    assert values["industrial_site_300"][0] == 1


def test_feature_source_raw_features_shape(tmp_path):
    stores = tmp_path / "stores.csv"
    pd.DataFrame({
        "상호명": ["가게"], "상권업종중분류명": ["한식"], "상권업종소분류명": ["백반"],
        "경도": [126.978], "위도": [37.5665],
    }).to_csv(stores, index=False, encoding="utf-8-sig")
    roads = tmp_path / "roads.geojson"
    _write_geojson(roads, [{
        "type": "Feature", "properties": {"way_class": "primary"},
        "geometry": {"type": "LineString", "coordinates": [[126.978, 37.5665], [126.979, 37.5665]]},
    }])
    industrial = tmp_path / "industrial.geojson"
    _write_geojson(industrial, [])
    source = FeatureSource.load(stores, roads, industrial)
    points = project_points(np.array([126.9785]), np.array([37.5665]))
    raw = source.raw_features(points)
    assert list(raw.columns) == list(FEATURE_NAMES)
    assert raw.loc[0, "stores_300"] >= 1
    assert raw.loc[0, "major_road_m_300"] > 0
