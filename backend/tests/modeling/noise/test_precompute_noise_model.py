"""precompute integration: NOISE_MODEL=poi replaces only non-direct noise cells."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest
from shapely.geometry import box, mapping

from app.contracts.constants import DataStatus, TimeSlot
from app.modeling.noise import model
from app.services import precompute

ORIGIN_LNG, ORIGIN_LAT = 127.0, 37.5
FEATURES = ["a_pred", "poi_all_100m", "poi_pub_300m", "nearest_pub_m"]


def _write_processed(directory):
    boundary = box(ORIGIN_LNG - 0.005, ORIGIN_LAT - 0.0045, ORIGIN_LNG + 0.005, ORIGIN_LAT + 0.0045)
    (directory / "admin_dong_boundaries.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"dong_code": "1111051500"},
                        "geometry": mapping(boundary),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {"sensor_id": "S1", "source": "sdot", "lng": ORIGIN_LNG, "lat": ORIGIN_LAT,
             "name": "center", "gu_name": "", "dong_code": "", "installed_at": "",
             "location_status": "observed"},
        ]
    ).to_csv(directory / "sdot_sensors.csv", index=False)
    pd.DataFrame(
        [
            {"sensor_id": "S1", "observed_at": "2026-10-12T08:00:00+09:00", "noise_db": 55.0,
             "illuminance_lux": None, "sample_count": 1, "quality_flag": ""},
            {"sensor_id": "S1", "observed_at": "2026-10-12T23:00:00+09:00", "noise_db": 60.0,
             "illuminance_lux": None, "sample_count": 1, "quality_flag": ""},
        ]
    ).to_csv(directory / "sdot_observations.csv", index=False)


def _artifact(path):
    slots = {
        slot.value: {
            "intercept": 0.5,
            "weights": {"a_pred": 0.0, "poi_all_100m": 0.1, "poi_pub_300m": 0.05, "nearest_pub_m": -0.001},
            "impute": {"a_pred": 55.0, "poi_all_100m": 2.0, "poi_pub_300m": 1.0, "nearest_pub_m": 2000.0},
            "cv": {"mae": 6.0},
        }
        for slot in TimeSlot
    }
    artifact = model.build_artifact(
        model_id="noise_poi_test",
        model_version="0.0.1",
        created_at="2026-11-01T00:00:00+09:00",
        training={},
        features=[{"name": name, "kind": "numeric", "description": name} for name in FEATURES],
        slot_models=slots,
    )
    return model.write_artifact(path, artifact)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    _write_processed(tmp_path)
    osm_dir = tmp_path / "osm"
    osm_dir.mkdir()
    (osm_dir / "seoul_roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (osm_dir / "industrial_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    stores = tmp_path / "stores.csv"
    pd.DataFrame(
        [
            ["음식", "주점", "일반유흥주점", str(ORIGIN_LNG), str(ORIGIN_LAT)],
            ["음식", "일반음식점", "한식", str(ORIGIN_LNG + 0.001), str(ORIGIN_LAT)],
        ],
        columns=["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "경도", "위도"],
    ).to_csv(stores, index=False, encoding="utf-8-sig")
    monkeypatch.setenv("NOISE_POI_STORES_CSV", str(stores))
    monkeypatch.setenv("NOISE_OSM_DIR", str(osm_dir))
    monkeypatch.setenv("NOISE_MODEL_ARTIFACT", str(_artifact(tmp_path / "artifact.json")))
    return tmp_path


def _by_cell(snapshot):
    return {(cell.grid_id, cell.time_slot.value): cell for cell in snapshot.cells}


def test_off_is_the_default_and_never_uses_the_model(inputs):
    default, _ = precompute.build_snapshot(inputs)
    explicit, _ = precompute.build_snapshot(inputs, noise_model="off")
    for left, right in zip(default.cells, explicit.cells):
        assert (left.grid_id, left.time_slot) == (right.grid_id, right.time_slot)
        assert (left.noise.method, left.noise.raw_value) == (right.noise.method, right.noise.raw_value)
    assert {c.noise.method for c in default.cells} <= {"direct", "idw", None}
    assert any(c.noise.method == "idw" for c in default.cells)
    assert any(c.noise.status == DataStatus.missing for c in default.cells)


def test_poi_model_replaces_only_non_direct_cells(inputs):
    baseline, _ = precompute.build_snapshot(inputs)
    modelled, _ = precompute.build_snapshot(inputs, noise_model="poi")
    base = _by_cell(baseline)
    assert len(modelled.cells) == len(baseline.cells)

    direct = [cell for cell in modelled.cells if base[cell.grid_id, cell.time_slot.value].noise.method == "direct"]
    assert direct, "fixture must contain at least one cell with a direct sensor"
    for cell in direct:
        assert cell.noise.method == "direct"
        assert cell.noise.raw_value == pytest.approx(
            base[cell.grid_id, cell.time_slot.value].noise.raw_value
        )

    estimated = [cell for cell in modelled.cells if cell.noise.method == "model"]
    assert estimated, "cells outside the sensor cell must get a model prediction"
    for cell in estimated:
        assert cell.noise.status == DataStatus.estimated
        assert cell.noise.raw_unit == "dB"
        assert 0.0 < cell.noise.confidence <= 0.9
        assert "noise_poi_test" in (cell.noise.note or "")
        assert cell.noise.raw_value is not None
    # every non-direct cell is now a model prediction, including previously missing ones
    assert all(
        cell.noise.method == "model"
        for cell in modelled.cells
        if base[cell.grid_id, cell.time_slot.value].noise.method != "direct"
    )


def test_unknown_model_flag_is_rejected_before_work(inputs):
    with pytest.raises(ValueError, match="unsupported noise model"):
        precompute.build_snapshot(inputs, noise_model="live")


def test_poi_flag_requires_store_file(tmp_path, monkeypatch):
    _write_processed(tmp_path)
    osm_dir = tmp_path / "osm"
    osm_dir.mkdir()
    (osm_dir / "seoul_roads.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    (osm_dir / "industrial_areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    monkeypatch.setenv("NOISE_OSM_DIR", str(osm_dir))
    monkeypatch.setenv("NOISE_MODEL_ARTIFACT", str(_artifact(tmp_path / "artifact.json")))
    monkeypatch.delenv("NOISE_POI_STORES_CSV", raising=False)
    with pytest.raises(ValueError):
        precompute.build_snapshot(tmp_path, noise_model="poi")
