"""Inference contract of the L1 light model: determinism, pixel-mean preservation."""
from datetime import date

import pytest
from pyproj import Transformer
from shapely.geometry import box

from app.contracts.constants import GRID_CELL_SIZE_M, GRID_ORIGIN_X, GRID_ORIGIN_Y
from app.contracts.data_interfaces import NightLightRecord
from app.core import grid
from app.modeling.light.model import (
    METHOD,
    DominantPixelLayer,
    LightPoiModel,
    dasymetric_values,
    light_model_name,
)

TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)
PERIOD = (date(2026, 9, 1), date(2026, 9, 30))


def _cell_polygon(lng, lat):
    ix, iy = grid.cell_of(lng, lat)
    x = GRID_ORIGIN_X + ix * GRID_CELL_SIZE_M
    y = GRID_ORIGIN_Y + iy * GRID_CELL_SIZE_M
    return box(x, y, x + GRID_CELL_SIZE_M, y + GRID_CELL_SIZE_M)


def _pixel(pixel_id, lng, lat, radiance=100, quality=0):
    return NightLightRecord(
        pixel_id, lng, lat, 0.004167, "VNP46A3", *PERIOD, radiance, quality,
    )


def test_dasymetric_values_preserve_area_weighted_pixel_mean():
    rows = [
        {"grid_id": "a", "pixel_id": "p", "radiance": 100.0, "weight": 1.0, "overlap_area": 1.0},
        {"grid_id": "b", "pixel_id": "p", "radiance": 100.0, "weight": 2.0, "overlap_area": 1.0},
        {"grid_id": "c", "pixel_id": "p", "radiance": 100.0, "weight": 4.0, "overlap_area": 2.0},
    ]
    values = dasymetric_values(rows)
    weighted = sum(row["overlap_area"] * values[row["grid_id"]] for row in rows) / 4.0
    assert weighted == pytest.approx(100.0)
    assert values["a"] < values["b"] < values["c"]


def test_dasymetric_values_keep_pixels_independent():
    rows = [
        {"grid_id": "a", "pixel_id": "p", "radiance": 100.0, "weight": 1.0, "overlap_area": 1.0},
        {"grid_id": "b", "pixel_id": "p", "radiance": 100.0, "weight": 3.0, "overlap_area": 1.0},
        {"grid_id": "c", "pixel_id": "q", "radiance": 10.0, "weight": 2.0, "overlap_area": 1.0},
    ]
    values = dasymetric_values(rows)
    assert (values["a"] + values["b"]) / 2 == pytest.approx(100.0)
    assert values["c"] == pytest.approx(10.0)


def test_dominant_pixel_picks_largest_overlap():
    polygon = _cell_polygon(126.978, 37.5665)
    centre = polygon.centroid
    lng, lat = TO_WGS.transform(centre.x, centre.y)
    near = _pixel("near", lng, lat, radiance=50)
    far = _pixel("far", lng + 0.002, lat, radiance=500)
    assignment = DominantPixelLayer([near, far]).assign(polygon)
    assert assignment is not None and assignment.record.pixel_id == "near"
    assert assignment.covered_fraction == pytest.approx(1.0)


def test_dominant_pixel_tie_breaks_on_pixel_id():
    polygon = _cell_polygon(126.978, 37.5665)
    centre = polygon.centroid
    lng, lat = TO_WGS.transform(centre.x, centre.y)
    first = _pixel("b", lng, lat)
    second = _pixel("a", lng, lat)
    assert DominantPixelLayer([first, second]).assign(polygon).record.pixel_id == "a"


@pytest.mark.parametrize("quality", [2, 255])
def test_unusable_pixels_are_ignored(quality):
    polygon = _cell_polygon(126.978, 37.5665)
    centre = polygon.centroid
    lng, lat = TO_WGS.transform(centre.x, centre.y)
    assert DominantPixelLayer([_pixel("bad", lng, lat, quality=quality)]).assign(polygon) is None


def test_artifact_loads_and_is_deterministic():
    model = LightPoiModel.load()
    assert model.method == METHOD == "pixel_overlay_poi"
    assert model.model_version == "light-poi-v1"
    assert len(model.cell_exponent) == 15685
    assert set(model.coefficients) == {
        "stores_100", "stores_300", "nightlife_300", "late_night_300",
        "industrial_frac_300", "industrial_site_300", "major_road_m_300",
    }
    assert model.weight("missing-cell") == 1.0
    assert model.weight("SEL200-0132-0013") == pytest.approx(
        LightPoiModel.load().weight("SEL200-0132-0013")
    )


def test_light_model_flag_default_off(monkeypatch):
    monkeypatch.delenv("LIGHT_MODEL", raising=False)
    assert light_model_name() == "overlay"
    monkeypatch.setenv("LIGHT_MODEL", "POI")
    assert light_model_name() == "poi"
