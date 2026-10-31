"""Contract-format synthetic fixtures are test-only; no external API calls."""
import csv
import json
from dataclasses import asdict, fields
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from shapely.ops import transform
from pyproj import Transformer

from app.api.deps import get_provider
from app.config import Settings
from app.contracts.constants import DataStatus, SourceId, TimeSlot
from app.contracts.data_interfaces import SensorLocation, SensorObservation, LivingPopulationRecord, NightLightRecord
from app.core import grid
from app.main import create_app
from app.services.live_provider import LiveProvider
from app.services.precompute import KST, metric_cell, precompute

TO_WGS = Transformer.from_crs(5179, 4326, always_xy=True).transform
IX, IY = grid.cell_of(126.978, 37.5665)


def write_csv(path, record_type, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[f.name for f in fields(record_type)])
        writer.writeheader()
        for row in rows:
            record = asdict(row)
            writer.writerow({k: v.value if hasattr(v, "value") else v.isoformat() if hasattr(v, "isoformat") else v
                             for k, v in record.items()})


def make_processed(path):
    # Two adjacent cells plus 6 cells eastward: direct, IDW and >1 km missing.
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    boundary = transform(TO_WGS, box(x0+0.1, y0+0.1, x0+1599.9, y0+199.9))
    path.joinpath("admin_dong_boundaries.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(boundary),
        "properties": {"dong_code": "11110530", "dong_name": "test", "gu_name": "test"}}]}))
    a = TO_WGS(x0+30, y0+100)
    b = TO_WGS(x0+170, y0+100)
    sensors = [SensorLocation("A", SourceId.sdot, *a), SensorLocation("B", SourceId.sdot, *b)]
    write_csv(path/"sdot_sensors.csv", SensorLocation, sensors)
    rows = []
    for hour in (0, 7, 18, 22):
        for sid, noise, lux in (("A", 40, 10), ("B", 60, 30)):
            rows.append(SensorObservation(sid, datetime(2026, 10, 1, hour, tzinfo=KST), noise, lux, 12))
    rows.extend([
        SensorObservation("A", datetime(2026, 10, 1, 23, tzinfo=KST), 200, 999, 1, "out_of_range"),
        SensorObservation("orphan", datetime(2026, 10, 1, 22, tzinfo=KST), 99, 999, 1),
    ])
    write_csv(path/"sdot_observations.csv", SensorObservation, rows)
    write_csv(path/"living_population.csv", LivingPopulationRecord, [
        LivingPopulationRecord("11110530", date(2026, 10, 1), h, 3200, area_km2=0.32)
        for h in (0, 7, 18, 22)])
    centre = grid.cell_center(IX+7, IY)
    write_csv(path/"nasa_night_lights.csv", NightLightRecord, [
        NightLightRecord("p1", *centre, 0.004167, "VNP46A3", date(2026, 9, 1), date(2026, 9, 30), 20, 0),
        NightLightRecord("bad", *centre, 0.004167, "VNP46A3", date(2026, 9, 1), date(2026, 9, 30), 999, 2),
    ])
    return Settings(data_mode="live", processed_data_dir=str(path), database_url=None, _env_file=None)


@pytest.fixture
def live(tmp_path):
    settings = make_processed(tmp_path)
    snapshot = precompute(settings)
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client:
        yield client, provider, snapshot, settings


def inspect(client, ix=IX, slot="night", lnglat=None):
    lng, lat = lnglat or grid.cell_center(ix, IY)
    return client.get("/api/v1/location/inspect", params={"lng": lng, "lat": lat, "time_slot": slot})


def test_precompute_live_api(live):
    client, provider, snapshot, _ = live
    assert len(snapshot.grids) == 8 and len(snapshot.cells) == 32
    response = inspect(client)
    assert response.status_code == 200
    body = response.json()
    noise = body["indicators"]["noise"]
    assert noise["raw_value"] == 50 and noise["status"] == "observed" and noise["method"] == "direct"
    assert noise["observed_at"] == "2026-10-01T22:00:00+09:00"
    assert noise["period_start"] == "2026-10-01T00:00:00+09:00"
    assert body["indicators"]["crowd"]["raw_value"] == pytest.approx(10000)
    assert body["indicators"]["crowd"]["status"] == "estimated"
    assert body["meta"]["data_mode"] == "live" and body["data_status"] == "estimated"
    assert 0 < body["confidence"] < 1
    status = client.get("/api/v1/data/status").json()
    assert status["grid"]["cell_count"] == 8
    assert set(status["grid"]["precomputed_slots"]) == {s.value for s in TimeSlot}
    sdot = next(s for s in status["sources"] if s["source_id"] == "sdot")
    assert sdot["observation_count"] == 10 and sdot["missing_rate"] == pytest.approx(0.2)
    assert sdot["coverage"]["bbox"] != [126.76, 37.41, 127.19, 37.72]
    assert sdot["update_frequency"] is None
    assert client.get("/api/v1/health").json()["data_ready"] is True


def test_idw_cutoff_and_light_precedence(live):
    client, *_ = live
    adjacent = inspect(client, IX+1).json()["indicators"]
    assert adjacent["noise"]["status"] == "estimated" and adjacent["noise"]["method"] == "idw"
    assert 50 < adjacent["noise"]["raw_value"] < 60
    assert adjacent["light"]["raw_unit"] == "lux"
    distant = inspect(client, IX+7).json()["indicators"]
    assert distant["noise"]["raw_value"] is None and distant["noise"]["status"] == "missing"
    assert distant["light"]["raw_value"] == pytest.approx(20)
    assert distant["light"]["method"] == "pixel_overlay" and distant["light"]["raw_unit"] == "nW/cm2/sr"
    assert distant["light"]["status"] == "estimated" and distant["light"]["observed_at"] is None
    for slot in ("day", "evening"):
        light = inspect(client, IX+7, slot).json()["indicators"]["light"]
        assert light["raw_value"] is None
        assert light["status"] == ("not_applicable" if slot == "day" else "missing")
    assert inspect(client, IX+7, "dawn").json()["indicators"]["light"]["raw_value"] == pytest.approx(20)
    assert inspect(client, slot="day").json()["weights"]["light"] == 0


def test_click_evidence_is_reordered_without_mutation(live):
    client, provider, *_ = live
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    near_a, near_b = TO_WGS(x0+31, y0+100), TO_WGS(x0+169, y0+100)
    a = inspect(client, lnglat=near_a).json()["indicators"]["noise"]["nearby_sensors"]
    b = inspect(client, lnglat=near_b).json()["indicators"]["noise"]["nearby_sensors"]
    assert a[0]["sensor_id"] == "A" and b[0]["sensor_id"] == "B"
    assert a[0]["distance_m"] == pytest.approx(1, abs=0.1)
    assert inspect(client, lnglat=near_a).json()["indicators"]["noise"]["nearby_sensors"] == a


def test_grid_recommendations_and_missing(live):
    client, *_ = live
    lng, lat = grid.cell_center(IX, IY)
    bbox = f"{lng-.002},{lat-.002},{lng+.02},{lat+.002}"
    response = client.get("/api/v1/grid", params={"bbox": bbox, "time_slot": "night"})
    assert response.status_code == 200 and len(response.json()["features"]) == 8
    recommended = client.get("/api/v1/recommendations", params={"bbox": bbox, "time_slot": "night", "mode": "rest"}).json()
    assert recommended["items"] and recommended["meta"]["poi_available"] is False
    missing = inspect(client, IX+9, "day").json()
    assert missing["comfort_score"] is None and missing["data_status"] == "missing"
    assert missing["indicators"]["light"]["status"] == "not_applicable"


def test_unprepared_returns_503_but_health_is_readable(tmp_path):
    settings = Settings(data_mode="live", processed_data_dir=str(tmp_path), database_url=None, _env_file=None)
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client:
        response = inspect(client)
        assert response.status_code == 503 and response.json()["error"]["code"] == "data_unavailable"
        assert not (tmp_path/"cell_indicators.sqlite").exists()
        assert client.get("/api/v1/health").json()["status"] == "degraded"
        assert client.get("/api/v1/data/status").json()["grid"]["cell_count"] == 0
        precompute(make_processed(tmp_path))
        assert inspect(client).status_code == 200


def test_api_uses_only_snapshot_and_survives_input_removal(live):
    _, _, _, settings = live
    from pathlib import Path
    for path in Path(settings.processed_data_dir).glob("*.csv"):
        path.unlink()
    Path(settings.processed_data_dir, "admin_dong_boundaries.geojson").unlink()
    provider = LiveProvider(settings)
    assert provider.get_cells([grid.grid_id(IX, IY)], TimeSlot.night)


def test_invalid_inputs_do_not_replace_published_snapshot(live):
    _, _, _, settings = live
    from pathlib import Path
    directory = Path(settings.processed_data_dir)
    before = (directory/"cell_indicators.sqlite").read_bytes()
    (directory/"admin_dong_boundaries.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    with pytest.raises(ValueError):
        precompute(settings)
    assert (directory/"cell_indicators.sqlite").read_bytes() == before


def test_areal_weighting_and_partial_dong_coverage(tmp_path):
    settings = make_processed(tmp_path)
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    features = []
    # Half of the cell belongs to each dong; published densities are 10k and 30k.
    for code, left, right in (("a", x0+.001, x0+100), ("b", x0+100, x0+199.999)):
        features.append({"type": "Feature", "properties": {"dong_code": code},
                         "geometry": mapping(transform(TO_WGS, box(left, y0+.001, right, y0+199.999)))})
    (tmp_path/"admin_dong_boundaries.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    rows = [LivingPopulationRecord(code, date(2026, 10, 1), 22, pop, area_km2=0.02)
            for code, pop in (("a", 200), ("b", 600))]
    write_csv(tmp_path/"living_population.csv", LivingPopulationRecord, rows)
    snapshot = precompute(settings)
    crowd = next(c.crowd for c in snapshot.cells if c.time_slot == TimeSlot.night)
    assert crowd.raw_value == pytest.approx(20000, rel=1e-5)
    confidence = crowd.confidence
    write_csv(tmp_path/"living_population.csv", LivingPopulationRecord, rows[:1])
    snapshot = precompute(settings)
    partial = next(c.crowd for c in snapshot.cells if c.time_slot == TimeSlot.night)
    assert partial.raw_value == pytest.approx(10000)
    assert partial.confidence == pytest.approx(confidence/2, abs=0.0001)


def test_estimated_sensor_positions_never_become_observed(tmp_path):
    settings = make_processed(tmp_path)
    point = grid.cell_center(IX, IY)
    write_csv(tmp_path/"sdot_sensors.csv", SensorLocation,
              [SensorLocation("A", SourceId.sdot, *point, location_status=DataStatus.estimated)])
    snapshot = precompute(settings)
    value = next(c.noise for c in snapshot.cells if c.grid_id == grid.grid_id(IX,IY) and c.time_slot == TimeSlot.night)
    assert value.status == DataStatus.estimated and value.method == "direct"


def test_hourly_mean_does_not_sample_weight_and_uses_kst(tmp_path):
    settings = make_processed(tmp_path)
    write_csv(tmp_path/"sdot_observations.csv", SensorObservation, [
        # UTC 13:00 = KST 22:00. Both rows have equal weight despite raw sample counts.
        SensorObservation("A", datetime.fromisoformat("2026-10-01T13:00:00+00:00"), 40, None, 1),
        SensorObservation("A", datetime.fromisoformat("2026-10-01T23:00:00+09:00"), 60, None, 100),
    ])
    snapshot = precompute(settings)
    value = next(c.noise for c in snapshot.cells if c.grid_id == grid.grid_id(IX,IY) and c.time_slot == TimeSlot.night)
    assert value.raw_value == 50 and value.period_start.hour == 22


def test_nasa_latest_quality_and_lux_priority(tmp_path):
    settings = make_processed(tmp_path)
    centre = grid.cell_center(IX, IY)
    rows = [NightLightRecord("pixel", *centre, .004167, "VNP46A3", date(2026, m, 1), date(2026, m, 28), value, qf)
            for m, value, qf in ((7, 5, 0), (8, 15, 1), (9, 999, 2))]
    write_csv(tmp_path/"nasa_night_lights.csv", NightLightRecord, rows)
    snapshot = precompute(settings)
    cell = next(c for c in snapshot.cells if c.grid_id == grid.grid_id(IX,IY) and c.time_slot == TimeSlot.night)
    assert cell.light.raw_value == 20 and cell.light.raw_unit == "lux"
    (tmp_path/"sdot_observations.csv").unlink()
    snapshot = precompute(settings)
    cell = next(c for c in snapshot.cells if c.grid_id == grid.grid_id(IX,IY) and c.time_slot == TimeSlot.night)
    assert cell.light.raw_value == pytest.approx(15)
    assert cell.light.period_start.month == 8 and cell.light.confidence <= .44


def test_boundary_hole_excludes_cells(tmp_path):
    settings = make_processed(tmp_path)
    from shapely.geometry import Polygon
    x0, y0, _, _ = metric_cell(IX, IY).bounds
    outer = box(x0+.01, y0+.01, x0+1599.99, y0+599.99)
    hole = box(x0+199.99, y0+199.99, x0+400.01, y0+400.01)
    geom = transform(TO_WGS, Polygon(outer.exterior.coords, [hole.exterior.coords]))
    (tmp_path/"admin_dong_boundaries.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"dong_code": "11110530"}, "geometry": mapping(geom)}]}))
    snapshot = precompute(settings)
    assert grid.grid_id(IX+1, IY+1) not in {g["grid_id"] for g in snapshot.grids}


@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf"])
def test_nonfinite_input_rejected(tmp_path, bad_value):
    settings = make_processed(tmp_path)
    path = tmp_path/"sdot_observations.csv"
    text = path.read_text().replace(",40,", f",{bad_value},")
    path.write_text(text)
    with pytest.raises(ValueError, match="invalid record"):
        precompute(settings)


def test_live_dependency_selection_without_override(tmp_path, monkeypatch):
    settings = make_processed(tmp_path)
    precompute(settings)
    from app.config import get_settings
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("PROCESSED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    get_provider.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert inspect(client).json()["meta"]["data_mode"] == "live"
    finally:
        get_settings.cache_clear()
        get_provider.cache_clear()


def test_database_failure_does_not_fallback_or_expose_details(live, monkeypatch):
    _, _, _, settings = live
    # Placeholder, never an actual secret/connection string.
    settings = settings.model_copy(update={"database_url": "configured"})
    from app.db import postgres
    def unavailable(*args):
        raise RuntimeError("private-driver-detail")
    monkeypatch.setattr(postgres, "read_database_snapshot", unavailable)
    monkeypatch.setattr(postgres, "connect", unavailable)
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as client:
        response = inspect(client)
        assert response.status_code == 503 and "private-driver-detail" not in response.text
        health = client.get("/api/v1/health").json()
        assert health["database"]["status"] == "error" and not health["data_ready"]
