"""Inspect response presentation: mock source labels, raw-value rounding, crowd period note."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.contracts.data_interfaces import IndicatorValue, LivingPopulationRecord
from app.contracts.constants import DataStatus, SourceId
from app.main import create_app
from app.services import query_service
from app.services.live_provider import LiveProvider
from app.services.precompute import precompute
from app.contracts.constants import TimeSlot
from app.core import grid
from tests.api.test_live_api import IX, IY, inspect, live, make_processed, write_csv  # noqa: F401 (fixture)
from tests.conftest import NIGHT

PERIOD_NOTE = "과 다릅니다."
CROWD_PREFIX = "혼잡도는 "


def _client(settings):
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    return TestClient(app)


def test_mock_source_names_are_labelled(client):
    body = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.978, **NIGHT}).json()
    for detail in body["indicators"].values():
        if detail["status"] == "mock" and detail["source"]:
            assert detail["source_name"].endswith(" (mock)")
    assert body["indicators"]["noise"]["source_name"] == "서울 S-DoT 도시데이터 센서 (mock)"


def test_live_source_names_are_not_labelled(live):
    client, *_ = live
    noise = inspect(client).json()["indicators"]["noise"]
    assert noise["source_name"] == "서울 S-DoT 도시데이터 센서"


@pytest.mark.parametrize("value,unit,expected", [
    (52.61864265727394, "dB", 52.6), (12.349, "lux", 12.3), (20.06, "nW/cm2/sr", 20.1),
    (10234.6, "persons/km2", 10235.0), (1.23456, "other", 1.23456), (None, "dB", None),
])
def test_round_raw(value, unit, expected):
    assert query_service._round_raw(value, unit) == expected


def test_mock_inspect_raw_values_are_rounded(client):
    body = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.978, **NIGHT}).json()
    values = []
    for detail in body["indicators"].values():
        values.append((detail["raw_value"], detail["raw_unit"]))
        values.extend((s["raw_value"], s["raw_unit"]) for s in detail["nearby_sensors"])
    assert any(unit == "dB" for _, unit in values)
    for value, unit in values:
        if value is None:
            continue
        digits = 0 if unit == "persons/km2" else 1
        assert value == round(value, digits)


def test_live_idw_value_rounded_but_snapshot_untouched(live):
    client, provider, snapshot, _ = live
    noise = inspect(client, IX + 1).json()["indicators"]["noise"]
    assert noise["raw_value"] == round(noise["raw_value"], 1)
    stored = next(c.noise.raw_value for c in snapshot.cells
                  if c.grid_id == grid.grid_id(IX + 1, IY) and c.time_slot == TimeSlot.night)
    assert stored != round(stored, 1)  # precomputed value keeps full precision
    assert noise["raw_value"] == round(stored, 1)


def test_crowd_period_note_when_population_is_stale(tmp_path):
    settings = make_processed(tmp_path)
    write_csv(tmp_path / "living_population.csv", LivingPopulationRecord, [
        LivingPopulationRecord("11110530", date(2026, 8, d), h, 3200, area_km2=0.32)
        for d in (1, 2) for h in (0, 7, 18, 22)])
    precompute(settings)
    with _client(settings) as client:
        notes = inspect(client).json()["notes"]
    assert [n for n in notes if n.startswith(CROWD_PREFIX)] == [
        "혼잡도는 2026년 8월(2026-08-01~2026-08-02) 생활인구 기준의 과거 자료로, "
        "소음 측정 기간(2026-10-01~2026-10-01)과 다릅니다."]


def test_no_crowd_period_note_when_periods_align(live):
    client, *_ = live
    notes = inspect(client).json()["notes"]
    assert [n for n in notes if n.startswith(CROWD_PREFIX)] == [
        "혼잡도는 2026년 10월(2026-10-01~2026-10-01) 생활인구 기준의 과거 자료입니다."]


def test_no_crowd_period_note_in_mock_mode(client):
    notes = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.978, **NIGHT}).json()["notes"]
    assert not any(PERIOD_NOTE in n for n in notes)


def test_crowd_period_note_threshold():
    from datetime import datetime, timedelta
    from app.contracts.data_interfaces import CellIndicators
    from app.services.precompute import KST
    start = datetime(2026, 10, 1, tzinfo=KST)

    def cell(gap_days):
        crowd_end = start - timedelta(days=gap_days)
        crowd = IndicatorValue(1.0, "persons/km2", DataStatus.estimated, 0.5, source=SourceId.living_population,
                               period_start=crowd_end - timedelta(days=1), period_end=crowd_end)
        noise = IndicatorValue(50.0, "dB", DataStatus.observed, 0.9, source=SourceId.sdot, period_start=start,
                               period_end=start + timedelta(days=1))
        light = IndicatorValue(None, None, DataStatus.missing, 0.0)
        return CellIndicators(grid_id="x", time_slot=TimeSlot.night, noise=noise, light=light, crowd=crowd)

    assert query_service._crowd_note(cell(29)) == "혼잡도는 2026년 9월(2026-09-01~2026-09-01) 생활인구 기준의 과거 자료입니다."
    assert query_service._crowd_note(cell(30)) == ("혼잡도는 2026년 8월(2026-08-31~2026-08-31) 생활인구 기준의 과거 자료로, "
                                                  "소음 측정 기간(2026-10-01~2026-10-01)과 다릅니다.")
