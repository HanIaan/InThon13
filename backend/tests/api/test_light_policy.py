"""Light = NASA only, S-DoT lux as evidence, per-indicator quality flags, mock/live shape parity."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.contracts.constants import SourceId, TimeSlot
from app.contracts.data_interfaces import NightLightRecord, SensorLocation, SensorObservation
from app.core import grid
from app.main import create_app
from app.services.live_provider import LiveProvider
from app.services.mock_provider import MockProvider
from app.services.precompute import KST, flagged_indicators, precompute
from tests.api.test_live_api import IX, IY, inspect, make_processed, write_csv

NIGHT_SLOTS = ("evening", "night", "dawn")
SENSOR_SLOTS = ("evening", "night")  # fixture has S-DoT rows at 18h and 22h/0h, none at dawn


def _client(settings):
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    return TestClient(app)


def _with_pixel_at_ix(path, radiance=40, qf=0):
    """Fixture plus a NASA pixel covering the sensor cell IX."""
    centre = grid.cell_center(IX, IY)
    write_csv(path/"nasa_night_lights.csv", NightLightRecord, [
        NightLightRecord("p0", *centre, 0.004167, "VNP46A3", date(2026, 9, 1), date(2026, 9, 30), radiance, qf)])


def _observations(path, flag_a=None, lux_scale=1):
    rows = []
    for hour in (0, 7, 18, 22):
        for sid, noise, lux, flag in (("A", 40, 10, flag_a), ("B", 60, 30, None)):
            rows.append(SensorObservation(sid, datetime(2026, 10, 1, hour, tzinfo=KST), noise, lux * lux_scale, 12, flag))
    write_csv(path/"sdot_observations.csv", SensorObservation, rows)


def _read_sensors(path):
    from app.db.processed import load_processed
    return sorted(load_processed(str(path)).sensors, key=lambda s: s.sensor_id)


def _cell(snapshot, ix, slot):
    return next(c for c in snapshot.cells if c.grid_id == grid.grid_id(ix, IY) and c.time_slot == slot)


# ---------------------------------------------------------------- quality flags

@pytest.mark.parametrize("flag,excluded", [
    (None, set()), ("", set()),
    ("light_out_of_range", {"light"}), ("light_stuck_value", {"light"}),
    ("noise_out_of_range", {"noise"}), ("noise_stuck_value", {"noise"}),
    ("light_stuck_value;noise_out_of_range", {"noise", "light"}),
    ("duplicate_merged", {"noise", "light"}), ("no_valid_location", {"noise", "light"}),
    ("out_of_range", {"noise", "light"}), ("light_out_of_range;mystery", {"noise", "light"}),
])
def test_flag_tokens(flag, excluded):
    assert flagged_indicators(flag) == excluded


def test_light_flag_keeps_normal_noise(tmp_path):
    settings = make_processed(tmp_path)
    _observations(tmp_path, flag_a="light_out_of_range")
    snapshot = precompute(settings, local_only=True)
    noise = _cell(snapshot, IX, TimeSlot.night).noise
    assert noise.raw_value == 50 and {e.sensor_id for e in noise.evidence} == {"A", "B"}
    lux_ids = {e.sensor_id for e in snapshot.sensors["night", "light"]}
    assert lux_ids == {"B"}


def test_noise_flag_keeps_lux_evidence(tmp_path):
    settings = make_processed(tmp_path)
    _observations(tmp_path, flag_a="noise_stuck_value")
    snapshot = precompute(settings, local_only=True)
    assert _cell(snapshot, IX, TimeSlot.night).noise.raw_value == 60
    assert {e.sensor_id for e in snapshot.sensors["night", "light"]} == {"A", "B"}


@pytest.mark.parametrize("flag", ["duplicate_merged", "no_valid_location", "unknown_token"])
def test_row_level_flags_drop_both(tmp_path, flag):
    settings = make_processed(tmp_path)
    _observations(tmp_path, flag_a=flag)
    snapshot = precompute(settings, local_only=True)
    assert _cell(snapshot, IX, TimeSlot.night).noise.raw_value == 60
    assert {e.sensor_id for e in snapshot.sensors["night", "light"]} == {"B"}


# ---------------------------------------------------------------- NASA-only light

def test_nasa_light_identical_across_night_slots(tmp_path):
    settings = make_processed(tmp_path)
    _with_pixel_at_ix(tmp_path)
    snapshot = precompute(settings, local_only=True)
    lights = [_cell(snapshot, IX, TimeSlot(s)).light for s in NIGHT_SLOTS]
    for light in lights:
        assert light.raw_value == pytest.approx(40) and light.raw_unit == "nW/cm2/sr"
        assert light.method == "pixel_overlay" and light.status.value == "estimated"
        assert light.source.value == "nasa_black_marble" and light.observed_at is None
        assert light.period_start == datetime(2026, 9, 1, tzinfo=KST)
        assert light.period_end == datetime(2026, 10, 1, tzinfo=KST)
        assert "월간 위성 야간 방사휘도(~463m)를 200m 격자에 면적 배분한 추정값" in light.note
    assert len({(l.raw_value, l.confidence, l.period_start, l.period_end) for l in lights}) == 1
    day = _cell(snapshot, IX, TimeSlot.day).light
    assert day.raw_value is None and day.status.value == "not_applicable"


@pytest.mark.parametrize("qf", [2, 255])
def test_bad_quality_nasa_is_missing_not_lux(tmp_path, qf):
    settings = make_processed(tmp_path)
    _with_pixel_at_ix(tmp_path, qf=qf)
    precompute(settings, local_only=True)
    with _client(settings) as client:
        for slot in SENSOR_SLOTS:
            body = inspect(client, slot=slot).json()
            light = body["indicators"]["light"]
            assert light["raw_value"] is None and light["status"] == "missing" and light["score"] is None
            assert body["weights"]["light"] == 0
            assert light["nearby_sensors"] and {s["raw_unit"] for s in light["nearby_sensors"]} == {"lux"}


def test_lux_does_not_affect_score(tmp_path):
    settings = make_processed(tmp_path)
    _with_pixel_at_ix(tmp_path)

    def run(lux_scale):
        _observations(tmp_path, lux_scale=lux_scale)
        precompute(settings, local_only=True)
        with _client(settings) as client:
            return [inspect(client, slot=s).json() for s in SENSOR_SLOTS]

    low, high = run(1), run(500)
    for a, b in zip(low, high):
        assert a["comfort_score"] == b["comfort_score"]
        assert a["indicators"]["light"]["score"] == b["indicators"]["light"]["score"]
        assert a["indicators"]["light"]["raw_value"] == b["indicators"]["light"]["raw_value"] == pytest.approx(40)
        lux_a = {s["sensor_id"]: s["raw_value"] for s in a["indicators"]["light"]["nearby_sensors"]}
        lux_b = {s["sensor_id"]: s["raw_value"] for s in b["indicators"]["light"]["nearby_sensors"]}
        assert lux_a and all(lux_b[k] == pytest.approx(v * 500, rel=1e-3) for k, v in lux_a.items())
    with _client(settings) as client:
        scores = {inspect(client, slot=s).json()["indicators"]["light"]["score"] for s in NIGHT_SLOTS}
    assert len(scores) == 1  # same monthly value -> same light score in every night slot


def test_day_light_has_no_evidence(tmp_path):
    settings = make_processed(tmp_path)
    _with_pixel_at_ix(tmp_path)
    precompute(settings, local_only=True)
    with _client(settings) as client:
        light = inspect(client, slot="day").json()["indicators"]["light"]
    assert light["status"] == "not_applicable" and light["nearby_sensors"] == []


# ---------------------------------------------------------------- mock

def test_mock_light_matches_nasa_shape():
    mock = MockProvider()
    lng, lat = 126.978, 37.5665
    gid = grid.grid_id(*grid.cell_of(lng, lat))
    lights = [mock.get_cell_detail(gid, TimeSlot(s), lng, lat).light for s in NIGHT_SLOTS]
    for light in lights:
        assert light.raw_unit == "nW/cm2/sr" and light.source.value == "nasa_black_marble"
        assert light.method == "mock" and light.status.value == "mock" and light.observed_at is None
        assert light.evidence and {e.raw_unit for e in light.evidence} == {"lux"}
        assert {e.source.value for e in light.evidence} == {"sdot"}
    assert len({(l.raw_value, l.period_start, l.period_end) for l in lights}) == 1
    assert mock.get_cell_detail(gid, TimeSlot.day, lng, lat).light.status.value == "not_applicable"


# ---------------------------------------------------------------- mock/live parity

def _shape(value):
    """JSON structure: dict keys recursively, list element shape, leaf JSON type."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return type(value).__name__
    return "number"


@pytest.mark.parametrize("slot", ["night", "day"])
def test_mock_and_live_inspect_have_same_shape(tmp_path, client, slot):
    settings = make_processed(tmp_path)
    _with_pixel_at_ix(tmp_path)
    # Named sensors so nullable `name` has the same JSON type as mock evidence.
    sensors = [SensorLocation(f"{sid}", SourceId.sdot, s.lng, s.lat, name=f"sensor {sid}")
               for sid, s in zip("AB", _read_sensors(tmp_path))]
    write_csv(tmp_path/"sdot_sensors.csv", SensorLocation, sensors)
    precompute(settings, local_only=True)
    lng, lat = grid.cell_center(IX, IY)
    params = {"lng": lng, "lat": lat, "time_slot": slot}
    with _client(settings) as live_client:
        live = live_client.get("/api/v1/location/inspect", params=params).json()
    mock = client.get("/api/v1/location/inspect", params=params).json()
    assert live["comfort_score"] is not None and mock["comfort_score"] is not None
    live_shape, mock_shape = _shape(live), _shape(mock)
    # notes count differs by mode (mock vs live notices); compare their element type only.
    assert live_shape.pop("notes") == mock_shape.pop("notes") == ["str"]
    assert live_shape == mock_shape
    for ind in ("noise", "light", "crowd"):
        assert live["indicators"][ind]["raw_unit"] == mock["indicators"][ind]["raw_unit"]
        assert live["indicators"][ind]["source"] == mock["indicators"][ind]["source"]


def test_live_notes_always_state_crowd_is_july_history(tmp_path):
    from app.contracts.data_interfaces import LivingPopulationRecord
    settings = make_processed(tmp_path)
    write_csv(tmp_path/"living_population.csv", LivingPopulationRecord, [
        LivingPopulationRecord("11110530", date(2026, 7, d), h, 3200, area_km2=0.32)
        for d in range(25, 32) for h in (0, 7, 18, 22)])
    precompute(settings, local_only=True)
    with _client(settings) as client:
        for slot in ("day", "night"):
            notes = inspect(client, slot=slot).json()["notes"]
            # Exactly one crowd notice (history + noise-period difference merged).
            assert [n for n in notes if n.startswith("혼잡도는 ")] == [
                "혼잡도는 2026년 7월(2026-07-25~2026-07-31) 생활인구 기준의 과거 자료로, "
                "소음 측정 기간(2026-10-01~2026-10-01)과 다릅니다."]
    # Missing crowd (no population rows) still carries the historical-series notice.
    write_csv(tmp_path/"living_population.csv", LivingPopulationRecord, [])
    precompute(settings, local_only=True)
    with _client(settings) as client:
        notes = inspect(client, slot="night").json()["notes"]
        assert [n for n in notes if n.startswith("혼잡도는 ")] == [
            "혼잡도는 과거 생활인구 자료 기준입니다. 서울시 행정동 생활인구 시계열은 2026-07-31에 종료됐습니다."]
