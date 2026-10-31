"""Road model (NOISE_MODEL=road): packaged artifact, no store inputs, no extrapolation."""
from __future__ import annotations

import math

from app.contracts.constants import DataStatus, TimeSlot
from app.core import grid
from app.modeling.noise import model, service

from tests.modeling.noise.test_service import ORIGIN_LAT, ORIGIN_LNG, _records


def _road_predictor(records):
    predictor = service.NoisePoiPredictor(model.NoiseModel.load(service.ROAD_ARTIFACT), None, [], None)
    predictor.attach_sensors({slot.value: list(records) for slot in TimeSlot})
    ix, iy = grid.cell_of(ORIGIN_LNG, ORIGIN_LAT)
    far = (ix + 15, iy + 15)  # ~3 km from the only sensor
    predictor.prepare([(ix, iy), far])
    return predictor, (ix, iy), far


def test_packaged_road_artifact_uses_only_baseline_and_roads():
    road = model.NoiseModel.load(service.ROAD_ARTIFACT)
    assert road.model_id == "noise_road_ridge"
    assert not any(name.startswith("poi_") for name in road.features)
    assert "fallback" not in road.baseline
    for slot in TimeSlot:
        assert 0 < road.slot_mae(slot) < 7


def test_road_model_predicts_near_a_sensor(tmp_path):
    predictor, cell, _ = _road_predictor([_records()])
    value = predictor.value(*cell, TimeSlot.night)
    assert value.status == DataStatus.estimated and value.method == "model"
    assert math.isfinite(value.raw_value)
    assert "도로·철도" in value.note


def test_road_model_never_extrapolates_beyond_one_km(tmp_path):
    predictor, _, far = _road_predictor([_records()])
    assert predictor.value(*far, TimeSlot.night) is None


def test_road_flag_needs_no_store_file(monkeypatch):
    monkeypatch.delenv(service.STORES_ENV, raising=False)
    assert service.model_name("road") == "road"
    assert service.model_name("off") is None
    assert service.ARTIFACTS["road"] == service.ROAD_ARTIFACT
