"""Store (상가업소) POI feature tests — exact counts, caps, empty input."""
from __future__ import annotations

import pandas as pd
import pytest
from pyproj import Transformer

from app.modeling.noise import poi

_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)
_TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)

ORIGIN_LNG, ORIGIN_LAT = 127.0, 37.5


def _offset_point(east_m: float, north_m: float = 0.0) -> tuple[float, float]:
    """WGS84 point ``east_m`` metres east of the origin."""
    x, y = _TO_METRIC.transform(ORIGIN_LNG, ORIGIN_LAT)
    return _TO_WGS.transform(x + east_m, y + north_m)


def _store_frame(rows):
    """rows: (east_m, major, middle, small) -> the columns load_stores produces."""
    records = []
    for east, major, middle, small in rows:
        lng, lat = _offset_point(east)
        records.append(
            {
                "lng": lng,
                "lat": lat,
                "all": True,
                "food": major == "음식",
                "pub": middle == "주점",
                "karaoke": small == "노래방",
                "pc_bang": small == "PC방",
                "lodging": major == "숙박",
                "retail": major == "소매",
            }
        )
    return pd.DataFrame(records)


def test_feature_names_shape():
    names = poi.feature_names()
    assert len(names) == 7 * 3 + 2
    for category in poi.CATEGORIES:
        for radius in (50, 100, 300):
            assert f"poi_{category}_{radius}m" in names
    assert names[-2:] == ["nearest_pub_m", "nearest_karaoke_m"]


def test_store_field_counts_respect_radius():
    stores = _store_frame(
        [
            (40, "음식", "주점", "노래방"),
            (80, "음식", "일반음식점", "PC방"),
            (200, "소매", "소매", "편의점"),
            (400, "숙박", "숙박", "호텔"),
        ]
    )
    field = poi.StoreField(stores)
    features = field.features(ORIGIN_LNG, ORIGIN_LAT)
    assert features["poi_all_50m"] == 1
    assert features["poi_all_100m"] == 2
    assert features["poi_all_300m"] == 3
    assert features["poi_food_100m"] == 2
    assert features["poi_food_300m"] == 2
    assert features["poi_pub_300m"] == 1
    assert features["poi_karaoke_300m"] == 1
    assert features["poi_pc_bang_300m"] == 1
    assert features["poi_retail_300m"] == 1
    assert features["poi_lodging_300m"] == 0  # 400 m away
    assert features["nearest_pub_m"] == pytest.approx(40, abs=1e-6)
    assert features["nearest_karaoke_m"] == pytest.approx(40, abs=1e-6)


def test_nearest_distance_is_capped_when_absent():
    near = poi.StoreField(_store_frame([(900, "음식", "주점", "일반음식점")]), cap_m=2000)
    features = near.features(ORIGIN_LNG, ORIGIN_LAT)
    assert features["poi_pub_300m"] == 0
    assert features["nearest_pub_m"] == pytest.approx(900, abs=0.1)
    capped = poi.StoreField(_store_frame([(900, "음식", "일반음식점", "일반음식점")]), cap_m=500)
    assert capped.features(ORIGIN_LNG, ORIGIN_LAT)["nearest_pub_m"] == 500


def test_empty_stores_give_zero_counts_and_cap():
    points = pd.DataFrame([{"sensor_id": "p0", "lng": ORIGIN_LNG, "lat": ORIGIN_LAT}])
    empty = pd.DataFrame({name: [] for name in ["lng", "lat", *poi.CATEGORIES]})
    result = poi.compute_poi_features(points, empty, cap_m=1234)
    assert len(result) == 1
    assert set(result.columns) == {"sensor_id", *poi.feature_names()}
    for radius in (50, 100, 300):
        for category in poi.CATEGORIES:
            assert result.loc[0, f"poi_{category}_{radius}m"] == 0
    for category in poi.NEAREST_CATEGORIES:
        assert result.loc[0, f"nearest_{category}_m"] == 1234


def test_compute_poi_features_keeps_input_order():
    lng, lat = _offset_point(1000)
    points = pd.DataFrame(
        [
            {"sensor_id": "a", "lng": ORIGIN_LNG, "lat": ORIGIN_LAT},
            {"sensor_id": "b", "lng": lng, "lat": lat},
        ]
    )
    stores = _store_frame([(30, "음식", "주점", "노래방")])
    result = poi.compute_poi_features(points, stores)
    assert list(result["sensor_id"]) == ["a", "b"]
    assert result.loc[0, "poi_all_100m"] == 1
    assert result.loc[1, "poi_all_100m"] == 0


def test_cap_must_be_positive():
    with pytest.raises(ValueError):
        poi.StoreField(_store_frame([(10, "음식", "주점", "노래방")]), cap_m=0)


def test_load_stores_reads_categories_and_drops_missing_coordinates(tmp_path):
    path = tmp_path / "stores.csv"
    pd.DataFrame(
        [
            ["음식", "주점", "일반유흥주점", "127.0", "37.5"],
            ["음식", "일반음식점", "한식", "127.001", "37.501"],
            ["소매", "소매", "노래방", "127.002", "37.502"],
            ["숙박", "숙박", "호텔", "", "37.503"],
        ],
        columns=["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "경도", "위도"],
    ).to_csv(path, index=False, encoding="utf-8-sig")

    stores = poi.load_stores(path)
    assert len(stores) == 3  # the row without lng cannot be placed
    assert list(stores["pub"].astype(bool)) == [True, False, False]
    assert list(stores["food"].astype(bool)) == [True, True, False]
    assert list(stores["karaoke"].astype(bool)) == [False, False, True]
    assert stores["all"].all()


def test_load_stores_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        poi.load_stores(tmp_path / "absent.csv")
