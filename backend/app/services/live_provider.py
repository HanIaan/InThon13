"""Read-only, process-local cache of an explicitly published live snapshot.

DATABASE_URL selects PostGIS; otherwise use processed_data_dir/cell_indicators.sqlite.
There is no silent fallback from a failing configured DB to potentially stale files.
After a successful precompute, restart workers (or construct a new provider) to load
that generation. Raw processed input is never read here. A failed load is retried
on a later request so preparing the first dataset does not require an API restart.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree

from app.contracts.constants import LIGHT_APPLICABLE_SLOTS, SourceId, TimeSlot
from app.contracts.data_interfaces import SourceInfo
from app.contracts.errors import ApiError, ErrorCode
from app.db.snapshot import SNAPSHOT_NAME, read_snapshot

PROJECT = Transformer.from_crs(4326, 5179, always_xy=True).transform
HEALTH_TTL_S = 30.0
log = logging.getLogger(__name__)


class LiveProvider:
    data_mode = "live"

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._loaded = False
        self._cells = {}
        self._sources = []
        self._sensors = {}
        self._grid_ids = frozenset()
        self._health = None  # (checked_at monotonic, status, message)
        self._health_lock = threading.Lock()
        self.cell_count = 0

    def _load(self):
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                if self._settings.database_url:
                    from app.db.postgres import read_database_snapshot
                    snapshot = read_database_snapshot(self._settings.database_url, lean=True)
                else:
                    snapshot = read_snapshot(Path(self._settings.processed_data_dir) / SNAPSHOT_NAME)
                if not snapshot.cells or not snapshot.grids or not snapshot.sources:
                    raise ValueError("empty snapshot")
                cells = {(c.grid_id, c.time_slot): c for c in snapshot.cells}
                sensors = {}
                for key, evidence in snapshot.sensors.items():
                    points = [Point(PROJECT(e.lng, e.lat)) for e in evidence]
                    sensors[key] = (evidence, points, STRtree(points))
                self._cells = cells
                self._sources = snapshot.sources
                self._sensors = sensors
                self._grid_ids = frozenset(g["grid_id"] for g in snapshot.grids)
                self.cell_count = len(snapshot.grids)
                self._loaded = True
            except Exception:
                raise ApiError(ErrorCode.data_unavailable,
                               "Live data unavailable. Publish a precomputed dataset and check database configuration.") from None

    def warm(self):
        """Load the snapshot ahead of the first request. Failure is only logged; the next
        request retries through the normal _load path."""
        started = time.perf_counter()
        try:
            self._load()
        except Exception:
            # _load already replaces driver errors with a generic ApiError (no DSN details).
            log.warning("live data warm-up failed; requests will retry loading")
            return
        log.info("live data warm-up loaded %d cells in %.1fs", self.cell_count, time.perf_counter() - started)

    def covers(self, grid_id):
        """True only for precomputed cells (those intersecting the administrative boundary)."""
        self._load()
        return grid_id in self._grid_ids

    def get_cells(self, grid_ids, time_slot):
        self._load()
        return {gid: self._cells[gid, time_slot] for gid in grid_ids if (gid, time_slot) in self._cells}

    def _nearby(self, time_slot, indicator, lng, lat):
        evidence, points, tree = self._sensors.get((time_slot.value, indicator), ([], [], STRtree([])))
        point = Point(PROJECT(lng, lat))
        # Evidence is separately ranked at the click, not the cell centre. A 1 km
        # radius avoids presenting distant sensors as local corroboration.
        indices = sorted((int(i) for i in tree.query(point.buffer(1000)) if point.distance(points[i]) <= 1000),
                         key=lambda i: (point.distance(points[i]), evidence[i].sensor_id))[:8]
        return [replace(evidence[i], distance_m=round(point.distance(points[i]), 1)) for i in indices]

    def get_cell_detail(self, grid_id, time_slot, lng, lat):
        self._load()
        cell = self._cells.get((grid_id, time_slot))
        if cell is None:
            return None
        # Never mutate cached values when re-ranking evidence for a different click.
        values = {}
        for ind in ("noise", "light", "crowd"):
            value = getattr(cell, ind)
            if ind == "light":
                # Light is scored from NASA only; nearby S-DoT lux is supporting evidence.
                nearby = self._nearby(time_slot, ind, lng, lat) if time_slot in LIGHT_APPLICABLE_SLOTS else []
            elif value.source == SourceId.sdot:
                nearby = self._nearby(time_slot, ind, lng, lat)
            else:
                nearby = list(value.evidence)
            values[ind] = replace(value, evidence=nearby)
        return replace(cell, **values)

    def get_sources(self):
        try:
            self._load()
        except ApiError:
            return [SourceInfo(source, source.value, "not_loaded", None, 0, None,
                               notes="사전계산 데이터가 준비되지 않았거나 읽을 수 없습니다.") for source in SourceId]
        return list(self._sources)

    def precomputed_slots(self):
        try:
            self._load()
        except ApiError:
            return []
        return [slot for slot in TimeSlot if any(s == slot for _, s in self._cells)]

    def check_database(self):
        if not self._settings.database_url:
            return "disabled", "local precomputed SQLite backend"
        # Opening a connection costs seconds on a remote DB; cache the result (incl. errors).
        with self._health_lock:
            now = time.monotonic()
            if self._health and now - self._health[0] < HEALTH_TTL_S:
                return self._health[1], self._health[2]
            try:
                from app.db.postgres import connect
                with connect(self._settings.database_url) as conn:
                    conn.execute("SELECT 1")
                result = ("connected", None)
            except Exception:
                result = ("error", "database connection unavailable")
            self._health = (time.monotonic(), *result)
            return result

    def has_poi(self):
        return False
