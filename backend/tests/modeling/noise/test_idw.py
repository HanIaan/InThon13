"""IDW reproduction must match services.precompute.SensorField."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from pyproj import Transformer

from app.contracts.constants import DataStatus, SourceId
from app.contracts.data_interfaces import EvidenceSensor
from app.modeling.noise.idw import NoiseIDW, idw_predictions
from app.services.precompute import SensorField

_TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True)


def _lnglat(x, y):
    lng, lat = _TO_WGS.transform(x, y)
    return lng, lat


def _evidence(sensor_id, lng, lat, value):
    return EvidenceSensor(
        sensor_id=sensor_id,
        source=SourceId.sdot,
        lng=lng,
        lat=lat,
        distance_m=0.0,
        raw_value=value,
        raw_unit="dB",
        observed_at=datetime(2026, 10, 12, 8),
        name=None,
    )


def _precompute_field(records):
    return SensorField(
        [
            (_evidence(sid, lng, lat, value), 24, datetime(2026, 10, 12, 8), DataStatus.observed)
            for sid, lng, lat, value in records
        ]
    )


def _cell_metric_point(ix, iy, dx, dy):
    """WGS84 (lng, lat) of the metric point at cell (ix, iy) offset (dx, dy) metres."""
    from app.contracts.constants import GRID_ORIGIN_X, GRID_ORIGIN_Y

    return _lnglat(GRID_ORIGIN_X + ix * 200 + dx, GRID_ORIGIN_Y + iy * 200 + dy)


def test_direct_branch_matches_precompute():
    ix, iy = 210, 90
    records = [
        ("A", *_cell_metric_point(ix, iy, 40, 40), 50.0),
        ("B", *_cell_metric_point(ix, iy, 120, 150), 62.0),
    ]
    expected = _precompute_field(records).value(ix, iy, 1000.0)
    field = NoiseIDW([(lng, lat, value, sid) for sid, lng, lat, value in records])
    # Predict exactly at the metric cell centroid, as precompute does.
    from app.contracts.constants import GRID_ORIGIN_X, GRID_ORIGIN_Y

    lng, lat = _lnglat(GRID_ORIGIN_X + (ix + 0.5) * 200, GRID_ORIGIN_Y + (iy + 0.5) * 200)
    assert field.predict(lng, lat) == pytest.approx(expected.raw_value, abs=1e-3)
    assert expected.status == DataStatus.observed
    assert field.predict(lng, lat) == pytest.approx(56.0)  # equal weights inside the cell


def test_idw_branch_matches_precompute():
    ix, iy = 210, 90
    # Sensors in the 8 neighbouring cells, none in the query cell.
    offsets = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-2, 0), (2, 0),
               (-1, 1), (1, -1)]
    records = [
        (f"S{k}", *_cell_metric_point(ix + dx, iy + dy, 100, 100), 40.0 + 2 * k)
        for k, (dx, dy) in enumerate(offsets)
    ]
    expected = _precompute_field(records).value(ix, iy, 1000.0)
    field = NoiseIDW([(lng, lat, value, sid) for sid, lng, lat, value in records])
    from app.contracts.constants import GRID_ORIGIN_X, GRID_ORIGIN_Y

    lng, lat = _lnglat(GRID_ORIGIN_X + (ix + 0.5) * 200, GRID_ORIGIN_Y + (iy + 0.5) * 200)
    assert field.predict(lng, lat) == pytest.approx(expected.raw_value, abs=1e-3)
    assert expected.status == DataStatus.estimated
    assert len(expected.evidence) == 8  # capped at 8 nearest sensors


def test_no_sensor_in_range_returns_none():
    ix, iy = 210, 90
    records = [("far", *_cell_metric_point(ix + 20, iy + 20, 100, 100), 50.0)]
    field = NoiseIDW([(lng, lat, value, sid) for sid, lng, lat, value in records])
    from app.contracts.constants import GRID_ORIGIN_X, GRID_ORIGIN_Y

    lng, lat = _lnglat(GRID_ORIGIN_X + (ix + 0.5) * 200, GRID_ORIGIN_Y + (iy + 0.5) * 200)
    assert field.predict(lng, lat) is None
    assert _precompute_field(records).value(ix, iy, 1000.0).raw_value is None


def test_idw_predictions_per_slot():
    ix, iy = 210, 90
    train = pd.DataFrame(
        [
            {"sensor_id": "A", "lng": _cell_metric_point(ix - 1, iy, 100, 100)[0],
             "lat": _cell_metric_point(ix - 1, iy, 100, 100)[1], "slot": "day", "noise_db": 50.0},
            {"sensor_id": "A", "lng": _cell_metric_point(ix - 1, iy, 100, 100)[0],
             "lat": _cell_metric_point(ix - 1, iy, 100, 100)[1], "slot": "night", "noise_db": 40.0},
        ]
    )
    query = pd.DataFrame(
        [
            {"sensor_id": "Q", "lng": _cell_metric_point(ix, iy, 100, 100)[0],
             "lat": _cell_metric_point(ix, iy, 100, 100)[1], "slot": "day"},
            {"sensor_id": "Q", "lng": _cell_metric_point(ix, iy, 100, 100)[0],
             "lat": _cell_metric_point(ix, iy, 100, 100)[1], "slot": "night"},
        ]
    )
    values = idw_predictions(train, query)
    assert values[0] == pytest.approx(50.0)
    assert values[1] == pytest.approx(40.0)
