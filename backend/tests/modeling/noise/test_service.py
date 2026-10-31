"""Serving adapter tests: estimated/model output, evidence, fallback, env wiring."""
from __future__ import annotations

import json
import math
from datetime import datetime

import pandas as pd
import pytest

from app.contracts.constants import DataStatus, SourceId, TimeSlot
from app.contracts.data_interfaces import EvidenceSensor
from app.core import grid
from app.modeling.noise import model, service

FEATURES = ["a_pred", "poi_all_100m", "nearest_pub_m"]
ORIGIN_LNG, ORIGIN_LAT = 127.0, 37.5


def _artifact(path, *, mae=6.0):
    slots = {
        slot.value: {
            "intercept": 0.5,
            "weights": {"a_pred": 0.0, "poi_all_100m": 0.1, "nearest_pub_m": -0.001},
            "impute": {"a_pred": 55.0, "poi_all_100m": 2.0, "nearest_pub_m": 2000.0},
            "cv": {"mae": mae},
        }
        for slot in TimeSlot
    }
    artifact = model.build_artifact(
        model_id="test_model",
        model_version="0.0.1",
        created_at="2026-11-01T00:00:00+09:00",
        training={},
        features=[{"name": name, "kind": "numeric", "description": name} for name in FEATURES],
        slot_models=slots,
    )
    return model.write_artifact(path, artifact)


def _stores():
    return pd.DataFrame(
        [
            {"lng": ORIGIN_LNG, "lat": ORIGIN_LAT, "all": True, "food": True, "pub": True,
             "karaoke": False, "pc_bang": False, "lodging": False, "retail": False},
        ]
    )


def _records(sensor_id="S1", value=55.0, lng=ORIGIN_LNG, lat=ORIGIN_LAT):
    evidence = EvidenceSensor(
        sensor_id=sensor_id, source=SourceId.sdot, lng=lng, lat=lat, distance_m=0.0,
        raw_value=value, raw_unit="dB", observed_at=datetime(2026, 10, 12, 8), name="a",
    )
    return (evidence, 24, datetime(2026, 10, 12, 8), DataStatus.observed)


def _predictor(tmp_path, *, records=None, offset=15):
    path = _artifact(tmp_path / "artifact.json")
    predictor = service.NoisePoiPredictor(model.NoiseModel.load(path), _stores(), [], [])
    predictor.attach_sensors({slot.value: list(records or []) for slot in TimeSlot})
    ix, iy = grid.cell_of(ORIGIN_LNG, ORIGIN_LAT)
    far = (ix + offset, iy + offset)  # 15 cells ~ 3 km: inside 5 km, outside 1 km
    predictor.prepare([(ix, iy), far])
    return predictor, (ix, iy), far


def test_value_is_estimated_and_never_observed(tmp_path):
    predictor, cell, _ = _predictor(tmp_path, records=[_records()])
    value = predictor.value(*cell, TimeSlot.night)
    assert value.status == DataStatus.estimated
    assert value.method == "model"
    assert value.raw_unit == "dB"
    assert value.source == SourceId.sdot
    assert value.raw_value is not None and math.isfinite(value.raw_value)
    assert 0.0 < value.confidence <= 0.9
    assert "test_model" in value.note and "CV MAE" in value.note


def test_prediction_is_baseline_plus_artifact_residual(tmp_path):
    predictor, cell, _ = _predictor(tmp_path, records=[_records(value=57.0)])
    lng, lat = grid.cell_center(*cell)
    a_pred, fallback = predictor._baseline("night", lng, lat)
    assert not fallback
    row = predictor.features.loc[predictor.keys[cell]]
    expected = (
        a_pred + 0.5
        + 0.0 * a_pred
        + 0.1 * float(row["poi_all_100m"])
        - 0.001 * float(row["nearest_pub_m"])
    )
    assert predictor.value(*cell, "night").raw_value == pytest.approx(expected)


def test_evidence_lists_the_sensors_behind_the_baseline(tmp_path):
    predictor, cell, _ = _predictor(tmp_path, records=[_records()])
    value = predictor.value(*cell, TimeSlot.dawn)
    assert [e.sensor_id for e in value.evidence] == ["S1"]
    assert value.evidence[0].distance_m <= 200.0
    assert value.observed_at == datetime(2026, 10, 12, 8)
    assert value.period_start == datetime(2026, 10, 12, 8)
    assert value.period_end == datetime(2026, 10, 12, 9)


def test_cell_outside_primary_radius_uses_the_5km_fallback(tmp_path):
    predictor, _, far = _predictor(tmp_path, records=[_records(value=57.0)])
    lng, lat = grid.cell_center(*far)
    a_pred, fallback = predictor._baseline("night", lng, lat)
    assert fallback and a_pred is not None
    value = predictor.value(*far, TimeSlot.night)
    assert value.status == DataStatus.estimated
    assert [e.sensor_id for e in value.evidence] == ["S1"]
    assert "5 km" in value.note
    assert value.confidence == pytest.approx(0.3)  # 1 - 6/12, then x 0.6
    row = predictor.features.loc[predictor.keys[far]]
    expected = a_pred + 0.5 + 0.1 * float(row["poi_all_100m"]) - 0.001 * float(row["nearest_pub_m"])
    assert value.raw_value == pytest.approx(expected)


def test_cell_beyond_the_fallback_radius_uses_training_medians(tmp_path):
    predictor, _, far = _predictor(tmp_path, records=[_records()], offset=60)  # ~17 km away
    lng, lat = grid.cell_center(*far)
    a_pred, fallback = predictor._baseline("night", lng, lat)
    assert fallback and a_pred is None
    value = predictor.value(*far, TimeSlot.night)
    assert value.evidence == []
    row = predictor.features.loc[predictor.keys[far]]
    expected = 55.0 + 0.5 + 0.1 * float(row["poi_all_100m"]) - 0.001 * float(row["nearest_pub_m"])
    assert value.raw_value == pytest.approx(expected)


def test_no_sensors_at_all_still_returns_a_labelled_estimate(tmp_path):
    predictor, cell, _ = _predictor(tmp_path)
    value = predictor.value(*cell, TimeSlot.evening)
    assert value.status == DataStatus.estimated
    assert value.evidence == []
    assert value.raw_value is not None


def test_prepare_is_required_before_value(tmp_path):
    path = _artifact(tmp_path / "a.json")
    predictor = service.NoisePoiPredictor(model.NoiseModel.load(path), _stores(), [], [])
    with pytest.raises(RuntimeError):
        predictor.prepare([(0, 0)])


@pytest.mark.parametrize(
    "flag,expected",
    [("poi", True), ("POI", True), (" poi ", True), ("off", False), ("", False), (None, False), ("live", False)],
)
def test_parse_flag(flag, expected):
    assert service.parse_flag(flag) is expected


def test_predictor_from_env_off_returns_none(monkeypatch):
    monkeypatch.delenv(service.FLAG_ENV, raising=False)
    assert service.predictor_from_env("/tmp") is None
    monkeypatch.setenv(service.FLAG_ENV, "off")
    assert service.predictor_from_env("/tmp") is None


def test_predictor_from_env_requires_store_file(monkeypatch):
    monkeypatch.setenv(service.FLAG_ENV, "poi")
    monkeypatch.delenv(service.STORES_ENV, raising=False)
    with pytest.raises(ValueError):
        service.predictor_from_env("/tmp")


def test_predictor_from_env_loads_paths(monkeypatch, tmp_path):
    osm_dir = tmp_path / "osm"
    osm_dir.mkdir()
    (osm_dir / "seoul_roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (osm_dir / "industrial_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    stores = tmp_path / "stores.csv"
    pd.DataFrame(
        [["음식", "주점", "일반유흥주점", "127.0", "37.5"]],
        columns=["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "경도", "위도"],
    ).to_csv(stores, index=False, encoding="utf-8-sig")
    artifact = _artifact(tmp_path / "artifact.json")

    monkeypatch.setenv(service.FLAG_ENV, "poi")
    monkeypatch.setenv(service.STORES_ENV, str(stores))
    monkeypatch.setenv(service.OSM_DIR_ENV, str(osm_dir))
    monkeypatch.setenv(service.ARTIFACT_ENV, str(artifact))
    predictor = service.predictor_from_env(str(tmp_path))
    assert predictor is not None
    assert predictor.model.model_id == "test_model"
    assert len(predictor.stores) == 1


def test_predictor_from_env_missing_artifact_is_an_error(monkeypatch, tmp_path):
    osm_dir = tmp_path / "osm"
    osm_dir.mkdir()
    (osm_dir / "seoul_roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (osm_dir / "industrial_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    monkeypatch.setenv(service.ARTIFACT_ENV, str(_artifact(tmp_path / "artifact.json")))
    monkeypatch.setenv(service.FLAG_ENV, "poi")
    monkeypatch.setenv(service.STORES_ENV, str(tmp_path / "absent.csv"))
    monkeypatch.setenv(service.OSM_DIR_ENV, str(osm_dir))
    with pytest.raises(FileNotFoundError):
        service.predictor_from_env(str(tmp_path))
