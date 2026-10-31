"""Synthetic stress fixture, never published to the demo data directory.

Measures full in-process HTTP serialization for the contract's maximum bbox.
Snapshot loading is measured separately from steady-state interactive map requests.
Run: python -m pytest tests/api/test_live_performance.py -q -s
"""
from dataclasses import replace
from time import perf_counter

from fastapi.testclient import TestClient

from app.api.deps import get_provider
from app.core import grid
from app.db.snapshot import SNAPSHOT_NAME, Snapshot, write_snapshot
from app.main import create_app
from app.services.live_provider import LiveProvider
from app.services.precompute import build_snapshot
from tests.api.test_live_api import make_processed


def test_6000_cell_http_response_under_one_second(tmp_path):
    settings = make_processed(tmp_path)
    template, _ = build_snapshot(tmp_path)
    bbox = (126.90, 37.48, 127.065, 37.62)
    indices = grid.cells_in_bbox(bbox)
    assert len(indices) == 6000
    cells, grids = [], []
    for ix, iy in indices:
        gid = grid.grid_id(ix, iy)
        grids.append(dict(grid_id=gid, ix=ix, iy=iy,
                          geometry=dict(type="Polygon", coordinates=grid.cell_polygon(ix, iy))))
        cells.extend(replace(cell, grid_id=gid) for cell in template.cells[:4])
    write_snapshot(tmp_path/SNAPSHOT_NAME, Snapshot(cells, grids, template.sources, template.sensors))
    provider = LiveProvider(settings)
    app = create_app()
    app.dependency_overrides[get_provider] = lambda: provider
    params = {"bbox": ",".join(map(str, bbox)), "time_slot": "night"}
    with TestClient(app) as client:
        start = perf_counter()
        assert client.get("/api/v1/health").json()["data_ready"] is True
        load_seconds = perf_counter() - start
        start = perf_counter()
        response = client.get("/api/v1/grid", params=params)
        seconds = perf_counter() - start
    assert response.status_code == 200 and len(response.json()["features"]) == 6000
    print(f"6000 cells: snapshot_load={load_seconds:.3f}s, HTTP={seconds:.3f}s, bytes={len(response.content)}")
    assert seconds < 1.0
