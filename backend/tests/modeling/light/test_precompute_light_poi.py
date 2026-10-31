"""LIGHT_MODEL=poi integration: opt-in, L0 unchanged when off, pixel mean preserved."""
import json
from datetime import date

import numpy as np
import pytest
from pyproj import Transformer
from shapely import transform as shapely_transform
from shapely.geometry import box, mapping

from app.config import Settings
from app.contracts.constants import DataStatus, TimeSlot
from app.contracts.data_interfaces import (
    LivingPopulationRecord, NightLightRecord, SensorLocation, SensorObservation,
)
from app.core import grid
from app.modeling.light.model import METHOD, NOTE, project_geometry
from app.services.precompute import precompute, metric_cell
from tests.api.test_live_api import IX, IY, make_processed, write_csv

TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)
NIGHT_SLOTS = (TimeSlot.evening, TimeSlot.night, TimeSlot.dawn)


def _cell(snapshot, ix, iy, slot):
    return next(c for c in snapshot.cells
                if c.grid_id == grid.grid_id(ix, iy) and c.time_slot == slot)


def _block_fixture(path, radiance=100):
    """2x2 cells covered by one 463 m pixel centred on the block corner."""
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    boundary = box(x0 + 0.1, y0 + 0.1, x0 + 399.9, y0 + 399.9)
    (path / "admin_dong_boundaries.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "geometry": mapping(shapely_transform(
                boundary,
                lambda coords: np.column_stack(TO_WGS.transform(coords[:, 0], coords[:, 1])),
            )),
            "properties": {"dong_code": "11110530", "dong_name": "test", "gu_name": "test"},
        }],
    }))
    write_csv(path / "sdot_sensors.csv", SensorLocation, [])
    write_csv(path / "sdot_observations.csv", SensorObservation, [])
    write_csv(path / "living_population.csv", LivingPopulationRecord, [])
    lng, lat = TO_WGS.transform(x0 + 200, y0 + 200)
    write_csv(path / "nasa_night_lights.csv", NightLightRecord, [
        NightLightRecord("p", lng, lat, 0.004167, "VNP46A3",
                         date(2026, 9, 1), date(2026, 9, 30), radiance, 0),
    ])
    return Settings(data_mode="live", processed_data_dir=str(path), database_url=None, _env_file=None)


def test_poi_flag_switches_light_to_redistribution(tmp_path, monkeypatch):
    settings = make_processed(tmp_path)
    monkeypatch.setenv("LIGHT_MODEL", "poi")
    snapshot = precompute(settings, local_only=True)

    light = _cell(snapshot, IX + 7, IY, TimeSlot.night).light
    assert 15 < light.raw_value < 25  # pixel row redistributed over overlapping cells
    assert light.raw_unit == "nW/cm2/sr"
    assert light.method == METHOD == "pixel_overlay_poi"
    assert light.status is DataStatus.estimated and light.observed_at is None
    assert light.source.value == "nasa_black_marble"
    assert NOTE in light.note

    values = {_cell(snapshot, IX + 7, IY, slot).light.raw_value for slot in NIGHT_SLOTS}
    assert len(values) == 1
    assert _cell(snapshot, IX + 7, IY, TimeSlot.day).light.status is DataStatus.not_applicable

    # No valid NASA pixel over this cell: still missing, never S-DoT lux.
    assert _cell(snapshot, IX, IY, TimeSlot.night).light.status is DataStatus.missing


def test_default_flag_keeps_existing_overlay(tmp_path, monkeypatch):
    monkeypatch.delenv("LIGHT_MODEL", raising=False)
    settings = make_processed(tmp_path)
    snapshot = precompute(settings, local_only=True)
    assert _cell(snapshot, IX + 7, IY, TimeSlot.night).light.method == "pixel_overlay"


@pytest.mark.parametrize("quality", [2, 255])
def test_poi_bad_quality_pixel_stays_missing(tmp_path, monkeypatch, quality):
    settings = make_processed(tmp_path)
    centre = grid.cell_center(IX + 7, IY)
    write_csv(tmp_path / "nasa_night_lights.csv", NightLightRecord, [
        NightLightRecord("p1", *centre, 0.004167, "VNP46A3",
                         date(2026, 9, 1), date(2026, 9, 30), 20, quality),
    ])
    monkeypatch.setenv("LIGHT_MODEL", "poi")
    snapshot = precompute(settings, local_only=True)
    assert _cell(snapshot, IX + 7, IY, TimeSlot.night).light.status is DataStatus.missing


def test_poi_preserves_pixel_mean(tmp_path, monkeypatch):
    settings = _block_fixture(tmp_path)
    monkeypatch.setenv("LIGHT_MODEL", "poi")
    snapshot = precompute(settings, local_only=True)
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    lng, lat = TO_WGS.transform(x0 + 200, y0 + 200)
    pixel = project_geometry(box(lng - 0.004167 / 2, lat - 0.004167 / 2,
                                 lng + 0.004167 / 2, lat + 0.004167 / 2))
    cells = [metric_cell(IX + dx, IY + dy) for dx in (0, 1) for dy in (0, 1)]
    values = [next(c.light.raw_value for c in snapshot.cells
                   if c.grid_id == grid.grid_id(IX + dx, IY + dy)
                   and c.time_slot == TimeSlot.night)
              for dx in (0, 1) for dy in (0, 1)]
    assert all(value is not None for value in values)
    areas = [cell.intersection(pixel).area for cell in cells]
    assert sum(areas) > 0
    weighted = sum(area * value for area, value in zip(areas, values)) / sum(areas)
    assert weighted == pytest.approx(100.0, rel=1e-12)
    for dx in (0, 1):
        for dy in (0, 1):
            light = _cell(snapshot, IX + dx, IY + dy, TimeSlot.night).light
            assert light.method == METHOD and light.status is DataStatus.estimated
