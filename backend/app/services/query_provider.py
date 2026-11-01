"""Live provider for serverless hosts (LIVE_READ=query): reads only the requested cells from Postgres.

LiveProvider loads the whole published snapshot (~190k indicator rows) into memory at startup,
which is fast once warm but takes 30 s to 2 min per cold start. A serverless function may start
cold on any request, so this provider queries the cells each request needs instead and keeps
only small, rarely changing tables (sources, slots, sensor summaries, the set of covered grid
IDs) cached per process. Scoring is unchanged: query_service scores whatever cells come back.

Evidence handling matches LiveProvider: stored S-DoT/light evidence is not served; nearby
sensors are re-ranked at the click from sensor_summaries within 1 km.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import replace

from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree

from app.contracts.constants import LIGHT_APPLICABLE_SLOTS, SourceId, TimeSlot
from app.contracts.data_interfaces import CellIndicators, SourceInfo
from app.contracts.errors import ApiError, ErrorCode

log = logging.getLogger(__name__)
PROJECT = Transformer.from_crs(4326, 5179, always_xy=True).transform
NEARBY_M = 1000
MAX_EVIDENCE = 8

CELLS_SQL = """SELECT grid_id,time_slot,indicator,raw_value,raw_unit,status,confidence,method,source,
       observed_at,period_start,period_end,
       CASE WHEN source = 'sdot' OR indicator = 'light' THEN NULL ELSE evidence END AS evidence, note
  FROM public.cell_indicators WHERE time_slot = %s AND grid_id = ANY(%s)"""


class QueryProvider:
    data_mode = "live"

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._conn = None
        self._grid_ids: frozenset[str] | None = None
        self._sources: list[SourceInfo] | None = None
        self._slots: list[TimeSlot] | None = None
        self._sensors: dict | None = None
        self.cell_count = 0

    # ---- connection ---------------------------------------------------------
    def _connect(self):
        """Connection suited to a transaction pooler (e.g. Supabase port 6543).

        Many serverless instances each holding a session-pooler connection exhaust the pool
        ("max clients"), so QUERY_DB_PORT can point the same DATABASE_URL at the transaction
        pooler. There, session state does not survive between transactions, so prepared
        statements are off and settings are applied per transaction in _query (SET LOCAL)."""
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row

        params = conninfo_to_dict(self._settings.database_url)
        if self._settings.query_db_port:
            params["port"] = str(self._settings.query_db_port)
        return psycopg.connect(**params, connect_timeout=5, row_factory=dict_row, prepare_threshold=None)

    def _query(self, sql, params=()):
        """Run one read transaction, reconnecting once if the pooler dropped the connection.
        Driver errors are replaced by a generic ApiError so the DSN never reaches a response."""
        for attempt in (1, 2):
            try:
                with self._lock:
                    if self._conn is None or self._conn.closed:
                        self._conn = self._connect()
                    # Same session settings as app.db.postgres.connect, scoped to this transaction.
                    self._conn.execute("SET LOCAL TIME ZONE 'Asia/Seoul'")
                    self._conn.execute("SET LOCAL statement_timeout = '20s'")
                    rows = self._conn.execute(sql, params).fetchall()
                    self._conn.commit()
                    return rows
            except Exception:
                with self._lock:
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                if attempt == 2:
                    log.warning("live query failed")
                    raise ApiError(ErrorCode.data_unavailable,
                                   "Live data unavailable. Publish a precomputed dataset and check database configuration.") from None
        return []

    # ---- small cached tables ------------------------------------------------
    def _covered(self) -> frozenset[str]:
        if self._grid_ids is None:
            self._grid_ids = frozenset(r["grid_id"] for r in self._query("SELECT grid_id FROM public.grid_cells"))
            self.cell_count = len(self._grid_ids)
        return self._grid_ids

    def _sensor_index(self):
        if self._sensors is None:
            from app.db.postgres import EVIDENCE_ADAPTER

            grouped: dict = {}
            for row in self._query("SELECT time_slot, indicator, payload FROM public.sensor_summaries"):
                grouped.setdefault((row["time_slot"], row["indicator"]), []).append(EVIDENCE_ADAPTER.validate_python(row["payload"]))
            index = {}
            for key, evidence in grouped.items():
                points = [Point(PROJECT(e.lng, e.lat)) for e in evidence]
                index[key] = (evidence, points, STRtree(points))
            self._sensors = index
        return self._sensors

    def warm(self):
        """Nothing large to load; open the connection so the first request skips the handshake."""
        try:
            self._covered()
        except ApiError:
            log.warning("live query warm-up failed; requests will retry")

    # ---- DataProvider -------------------------------------------------------
    def covers(self, grid_id):
        return grid_id in self._covered()

    def _cells(self, grid_ids, time_slot):
        from app.db.postgres import _lean_value

        if not grid_ids:
            return {}
        grouped: dict = {}
        for row in self._query(CELLS_SQL, (time_slot.value, list(grid_ids))):
            gid, indicator = row.pop("grid_id"), row.pop("indicator")
            row.pop("time_slot")
            grouped.setdefault(gid, {})[indicator] = _lean_value(row)
        return {gid: CellIndicators(gid, time_slot, **values) for gid, values in grouped.items() if len(values) == 3}

    def get_cells(self, grid_ids, time_slot):
        return self._cells(grid_ids, time_slot)

    def _nearby(self, time_slot, indicator, lng, lat):
        evidence, points, tree = self._sensor_index().get((time_slot.value, indicator), ([], [], STRtree([])))
        point = Point(PROJECT(lng, lat))
        indices = sorted((int(i) for i in tree.query(point.buffer(NEARBY_M)) if point.distance(points[i]) <= NEARBY_M),
                         key=lambda i: (point.distance(points[i]), evidence[i].sensor_id))[:MAX_EVIDENCE]
        return [replace(evidence[i], distance_m=round(point.distance(points[i]), 1)) for i in indices]

    def get_cell_detail(self, grid_id, time_slot, lng, lat):
        cell = self._cells([grid_id], time_slot).get(grid_id)
        if cell is None:
            return None
        values = {}
        for ind in ("noise", "light", "crowd"):
            value = getattr(cell, ind)
            if ind == "light":
                nearby = self._nearby(time_slot, ind, lng, lat) if time_slot in LIGHT_APPLICABLE_SLOTS else []
            elif value.source == SourceId.sdot:
                nearby = self._nearby(time_slot, ind, lng, lat)
            else:
                nearby = list(value.evidence)
            values[ind] = replace(value, evidence=nearby)
        return replace(cell, **values)

    def get_sources(self):
        if self._sources is None:
            from app.db.postgres import SOURCE_ADAPTER

            try:
                self._sources = [SOURCE_ADAPTER.validate_python(r) for r in self._query("SELECT * FROM public.source_metadata")]
            except ApiError:
                return [SourceInfo(source, source.value, "not_loaded", None, 0, None,
                                   notes="사전계산 데이터가 준비되지 않았거나 읽을 수 없습니다.") for source in SourceId]
        return list(self._sources)

    def precomputed_slots(self):
        if self._slots is None:
            try:
                found = {r["time_slot"] for r in self._query("SELECT DISTINCT time_slot FROM public.cell_indicators")}
            except ApiError:
                return []
            self._slots = [slot for slot in TimeSlot if slot.value in found]
        return list(self._slots)

    def check_database(self):
        try:
            self._query("SELECT 1 AS ok")
            return "connected", None
        except ApiError:
            return "error", "database connection unavailable"

    def has_poi(self):
        return False
