"""Live hardening: warm-up, service-area coverage (404), cached DB health, lean PostGIS read.
No network: the PostGIS read is exercised with an in-memory fake connection."""
import json
import threading
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.deps import get_provider
from app.contracts.constants import SourceId
from app.core import grid
from app.db import postgres
from app.db.snapshot import EVIDENCE_ADAPTER, FORMAT_VERSION, SNAPSHOT_NAME
from app.main import create_app
from app.services import live_provider as lp
from app.services.live_provider import LiveProvider
from app.services.precompute import build_snapshot, precompute
from tests.api.test_live_api import IX, IY, live, make_processed  # noqa: F401 (fixture)

SEONGNAM = (127.1265, 37.4200)  # inside SEOUL_BBOX, outside the Seoul boundary


def _client(provider):
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    return TestClient(app)


# ---------------------------------------------------------------- coverage

def test_covers_only_precomputed_cells(live):
    client, provider, snapshot, _ = live
    assert provider.covers(grid.grid_id(IX, IY)) and provider.covers(grid.grid_id(IX + 7, IY))
    assert not provider.covers(grid.grid_id(IX + 9, IY))
    lng, lat = grid.cell_center(IX + 9, IY)
    r = client.get("/api/v1/location/inspect", params={"lng": lng, "lat": lat, "time_slot": "night"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "out_of_coverage"


def test_live_seongnam_is_404_but_mock_is_not(live, client):
    live_client, *_ = live
    lng, lat = SEONGNAM
    params = {"lng": lng, "lat": lat, "time_slot": "night"}
    r = live_client.get("/api/v1/location/inspect", params=params)
    assert r.status_code == 404 and r.json()["error"]["code"] == "out_of_coverage"
    assert client.get("/api/v1/location/inspect", params=params).status_code == 200  # mock: no covers()


def test_recommendations_center_coverage(live):
    client, *_ = live
    outside = grid.cell_center(IX + 9, IY)
    r = client.get("/api/v1/recommendations",
                   params={"lng": outside[0], "lat": outside[1], "radius_m": 500, "time_slot": "night", "mode": "rest"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "out_of_coverage"
    inside = grid.cell_center(IX, IY)
    r = client.get("/api/v1/recommendations",
                   params={"lng": inside[0], "lat": inside[1], "radius_m": 500, "time_slot": "night", "mode": "rest"})
    assert r.status_code == 200 and r.json()["items"]


# ---------------------------------------------------------------- warm-up

def test_warm_preloads_snapshot(tmp_path):
    settings = make_processed(tmp_path)
    precompute(settings, local_only=True)
    provider = LiveProvider(settings)
    provider.warm()
    assert provider.cell_count == 8
    (tmp_path / SNAPSHOT_NAME).unlink()  # served from memory after warm-up
    assert provider.get_cells([grid.grid_id(IX, IY)], lp.TimeSlot.night)


def test_warm_failure_is_logged_and_request_retries(tmp_path, caplog):
    settings = make_processed(tmp_path)
    provider = LiveProvider(settings)
    provider.warm()  # no snapshot yet: must not raise
    assert "warm-up failed" in caplog.text
    precompute(settings, local_only=True)
    lng, lat = grid.cell_center(IX, IY)
    with _client(provider) as client:
        r = client.get("/api/v1/location/inspect", params={"lng": lng, "lat": lat, "time_slot": "night"})
    assert r.status_code == 200


def test_startup_calls_warm(monkeypatch):
    called = threading.Event()

    class Warmable:
        def warm(self):
            called.set()

    monkeypatch.setattr(deps, "get_provider", lambda: Warmable())
    with TestClient(create_app()):
        assert called.wait(2)


# ---------------------------------------------------------------- DB health cache

class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        return None


def test_check_database_cached_for_ttl(tmp_path, monkeypatch):
    settings = make_processed(tmp_path).model_copy(update={"database_url": "configured"})  # placeholder, not a DSN
    calls = []
    monkeypatch.setattr(postgres, "connect", lambda url: calls.append(1) or _Conn())
    clock = [1000.0]
    monkeypatch.setattr(lp.time, "monotonic", lambda: clock[0])
    provider = LiveProvider(settings)
    assert provider.check_database() == ("connected", None)
    clock[0] += 29
    assert provider.check_database() == ("connected", None) and len(calls) == 1
    clock[0] += 2
    provider.check_database()
    assert len(calls) == 2


def test_check_database_error_cached_without_details(tmp_path, monkeypatch):
    settings = make_processed(tmp_path).model_copy(update={"database_url": "configured"})
    calls = []

    def fail(url):
        calls.append(1)
        raise RuntimeError("private-driver-detail")
    monkeypatch.setattr(postgres, "connect", fail)
    provider = LiveProvider(settings)
    assert provider.check_database() == ("error", "database connection unavailable")
    assert provider.check_database()[0] == "error" and len(calls) == 1


# ---------------------------------------------------------------- lean PostGIS read

class _Result(list):
    def fetchone(self):
        return self[0] if self else None


class FakeConn:
    """Answers the SELECTs of read_on_connection from a built snapshot, like PostgreSQL would."""

    def __init__(self, snapshot):
        self.snapshot, self.sql = snapshot, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        s = snapshot = self.snapshot
        if "engine_dataset" in sql:
            return _Result([{"format_version": FORMAT_VERSION}])
        if "cell_indicators" in sql:
            rows = []
            for cell in s.cells:
                for name in ("noise", "light", "crowd"):
                    v = getattr(cell, name)
                    evidence = [EVIDENCE_ADAPTER.dump_python(e, mode="json") for e in v.evidence]
                    if "CASE WHEN" in sql and (v.source == SourceId.sdot or name == "light"):
                        evidence = None
                    row = dict(grid_id=cell.grid_id, time_slot=cell.time_slot.value, indicator=name,
                               raw_value=v.raw_value, raw_unit=v.raw_unit, status=v.status.value,
                               confidence=v.confidence, method=v.method, source=v.source.value if v.source else None,
                               observed_at=v.observed_at, period_start=v.period_start, period_end=v.period_end,
                               evidence=evidence, note=v.note)
                    rows.append(row)
            return _Result(rows)
        if "grid_cells" in sql:
            keys = ("grid_id", "ix", "iy") if "geometry" not in sql else ("grid_id", "ix", "iy", "geometry")
            return _Result([{k: g[k] for k in keys} for g in snapshot.grids])
        if "source_metadata" in sql:
            return _Result([asdict(x) for x in snapshot.sources])
        if "sensor_summaries" in sql:
            return _Result([dict(time_slot=slot, indicator=ind, sensor_id=e.sensor_id,
                                 payload=EVIDENCE_ADAPTER.dump_python(e, mode="json"))
                            for (slot, ind), values in snapshot.sensors.items() for e in values])
        raise AssertionError(sql)


def test_lean_read_matches_snapshot_except_unused_evidence(tmp_path):
    make_processed(tmp_path)
    snapshot, _ = build_snapshot(tmp_path)
    conn = FakeConn(snapshot)
    loaded = postgres.read_on_connection(conn, lean=True)
    assert not any("ST_AsGeoJSON" in q or "SELECT *" in q and "cell_indicators" in q for q in conn.sql)
    expected = {(c.grid_id, c.time_slot): c for c in snapshot.cells}
    assert len(loaded.cells) == len(expected) and len(loaded.grids) == 8
    for cell in loaded.cells:
        want = expected[cell.grid_id, cell.time_slot]
        for name in ("noise", "light", "crowd"):
            got, ref = getattr(cell, name), getattr(want, name)
            dropped = ref.source == SourceId.sdot or name == "light"
            assert got.evidence == ([] if dropped else ref.evidence)
            ref_fields = {k: v for k, v in asdict(ref).items() if k != "evidence"}
            assert {k: v for k, v in asdict(got).items() if k != "evidence"} == ref_fields
    assert loaded.sensors == {k: v for k, v in snapshot.sensors.items() if v}


def test_live_provider_uses_lean_read_and_serves_click_evidence(tmp_path, monkeypatch):
    settings = make_processed(tmp_path).model_copy(update={"database_url": "configured"})
    snapshot, _ = build_snapshot(tmp_path)
    seen = {}

    def fake_read(url, **kwargs):
        seen.update(kwargs)
        return postgres.read_on_connection(FakeConn(snapshot), **kwargs)
    monkeypatch.setattr(postgres, "read_database_snapshot", fake_read)
    provider = LiveProvider(settings)
    lng, lat = grid.cell_center(IX, IY)
    with _client(provider) as client:
        body = client.get("/api/v1/location/inspect", params={"lng": lng, "lat": lat, "time_slot": "night"}).json()
    assert seen == {"lean": True}
    # Click evidence still comes from sensor summaries even though stored evidence was skipped.
    assert body["indicators"]["noise"]["nearby_sensors"] and body["indicators"]["light"]["nearby_sensors"]
    assert json.dumps(body)  # serializable
