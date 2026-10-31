"""PostGIS I/O via a server-only DATABASE_URL (owner or BYPASSRLS role).

Publication replaces only the tables owned by this engine in one transaction;
missing sources intentionally remove the previous generation's source rows. A
repeatable-read loader sees one complete generation. No SQL or driver exception
containing connection details is exposed by HTTP/CLI entrypoints.
"""
from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.contracts.constants import GRID_CELL_SIZE_M, GRID_ORIGIN_X, GRID_ORIGIN_Y, TimeSlot
from app.contracts.data_interfaces import CellIndicators, IndicatorValue
from app.db.snapshot import Snapshot, FORMAT_VERSION, SOURCE_ADAPTER, EVIDENCE_ADAPTER

VALUE_ADAPTER = TypeAdapter(IndicatorValue)
# Fixed advisory lock shared by all engine publishers/migrations, not user input.
LOCK_ID = 728619402


def connect(database_url):
    conn = psycopg.connect(database_url, connect_timeout=5, row_factory=dict_row)
    conn.execute("SET search_path TO public, extensions")
    conn.execute("SET TIME ZONE 'Asia/Seoul'")
    conn.execute("SET statement_timeout = '30s'")
    conn.commit()
    return conn


def publish(database_url, snapshot, data):
    with connect(database_url) as conn:
        publish_on_connection(conn, snapshot, data)


def publish_on_connection(conn, snapshot, data):
    """Caller owns transaction; exposed for rollback-isolated integration tests."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    # Cascades are avoided: deleting in dependency order cannot delete unrelated data.
    for table in ("cell_indicators", "sensor_summaries", "sdot_observations", "grid_cells",
                  "sensors", "admin_dong", "living_population", "night_lights", "source_metadata", "engine_dataset"):
        conn.execute(psycopg.sql.SQL("DELETE FROM public.{}").format(psycopg.sql.Identifier(table)))
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO public.sensors
            (sensor_id, source, geom, name, gu_name, dong_code, installed_at, location_status)
            VALUES (%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s,%s,%s,%s,%s)""",
            [(s.sensor_id, s.source.value, s.lng, s.lat, s.name, s.gu_name, s.dong_code, s.installed_at, s.location_status.value)
             for s in data.sensors])
        sensor_ids = {s.sensor_id for s in data.sensors}
        cur.executemany("""INSERT INTO public.sdot_observations
            (sensor_id, observed_at, noise_db, illuminance_lux, sample_count, quality_flag)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            [(r.sensor_id, r.observed_at, r.noise_db, r.illuminance_lux, r.sample_count, r.quality_flag)
             for r in data.observations if r.sensor_id in sensor_ids])
        cur.executemany("""INSERT INTO public.admin_dong (dong_code,dong_name,gu_name,geom)
            VALUES (%s,%s,%s,ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)))""",
            [(f["properties"]["dong_code"], f["properties"].get("dong_name"), f["properties"].get("gu_name"),
              json.dumps(f["geometry"])) for f in data.boundaries])
        cur.executemany("""INSERT INTO public.living_population
            (dong_code,stat_date,hour,total_population,dong_name,area_km2) VALUES (%s,%s,%s,%s,%s,%s)""",
            [(r.dong_code, r.stat_date, r.hour, r.total_population, r.dong_name, r.area_km2) for r in data.population])
        cur.executemany("""INSERT INTO public.night_lights
            (pixel_id,product,period_start,period_end,geom,pixel_size_deg,radiance,quality_flag,cloud_flag)
            VALUES (%s,%s,%s,%s,ST_MakeEnvelope(%s,%s,%s,%s,4326),%s,%s,%s,%s)""",
            [(r.pixel_id, r.product, r.period_start, r.period_end,
              r.lng-r.pixel_size_deg/2, r.lat-r.pixel_size_deg/2, r.lng+r.pixel_size_deg/2, r.lat+r.pixel_size_deg/2,
              r.pixel_size_deg, r.radiance, r.quality_flag, r.cloud_flag) for r in data.lights])
        cur.executemany("""INSERT INTO public.grid_cells (grid_id,ix,iy,geom,geom_5179)
            VALUES (%s,%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),ST_MakeEnvelope(%s,%s,%s,%s,5179))""",
            [(g["grid_id"], g["ix"], g["iy"], json.dumps(g["geometry"]),
              GRID_ORIGIN_X+g["ix"]*GRID_CELL_SIZE_M, GRID_ORIGIN_Y+g["iy"]*GRID_CELL_SIZE_M,
              GRID_ORIGIN_X+(g["ix"]+1)*GRID_CELL_SIZE_M, GRID_ORIGIN_Y+(g["iy"]+1)*GRID_CELL_SIZE_M) for g in snapshot.grids])
        rows = []
        for cell in snapshot.cells:
            for name in ("noise", "light", "crowd"):
                value = getattr(cell, name)
                rows.append((cell.grid_id, cell.time_slot.value, name, value.raw_value, value.raw_unit,
                             value.status.value, value.confidence, value.method, value.source.value if value.source else None,
                             value.observed_at, value.period_start, value.period_end,
                             Jsonb([EVIDENCE_ADAPTER.dump_python(e, mode="json") for e in value.evidence]), value.note))
        cur.executemany("""INSERT INTO public.cell_indicators
            (grid_id,time_slot,indicator,raw_value,raw_unit,status,confidence,method,source,
             observed_at,period_start,period_end,evidence,note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
        cur.executemany("""INSERT INTO public.source_metadata
            (source_id,name,status,last_updated_at,observation_count,missing_rate,coverage_bbox,
             coverage_description,temporal_start,temporal_end,spatial_resolution,update_frequency,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(s.source_id.value, s.name, s.status, s.last_updated_at, s.observation_count, s.missing_rate,
              Jsonb(s.coverage_bbox) if s.coverage_bbox else None, s.coverage_description, s.temporal_start,
              s.temporal_end, s.spatial_resolution, s.update_frequency, s.notes) for s in snapshot.sources])
        cur.executemany("INSERT INTO public.sensor_summaries VALUES (%s,%s,%s,%s)",
            [(slot, ind, e.sensor_id, Jsonb(EVIDENCE_ADAPTER.dump_python(e, mode="json")))
             for (slot, ind), values in snapshot.sensors.items() for e in values])
    conn.execute("INSERT INTO public.engine_dataset (format_version) VALUES (%s)", (FORMAT_VERSION,))


def read_database_snapshot(database_url):
    with connect(database_url) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return read_on_connection(conn)


def read_on_connection(conn):
    manifest = conn.execute("SELECT format_version FROM public.engine_dataset WHERE singleton").fetchone()
    if not manifest or manifest["format_version"] != FORMAT_VERSION:
        raise ValueError("precomputed dataset is unavailable or incompatible")
    grouped = {}
    for row in conn.execute("SELECT * FROM public.cell_indicators ORDER BY grid_id,time_slot,indicator"):
        gid, slot, indicator = row.pop("grid_id"), row.pop("time_slot"), row.pop("indicator")
        grouped.setdefault((gid, slot), {})[indicator] = VALUE_ADAPTER.validate_python(row)
    cells = [CellIndicators(gid, TimeSlot(slot), **values) for (gid, slot), values in grouped.items()]
    grids = list(conn.execute("SELECT grid_id,ix,iy,ST_AsGeoJSON(geom)::jsonb AS geometry FROM public.grid_cells"))
    sources = [SOURCE_ADAPTER.validate_python(row) for row in conn.execute("SELECT * FROM public.source_metadata")]
    sensors = {}
    for row in conn.execute("SELECT * FROM public.sensor_summaries"):
        sensors.setdefault((row["time_slot"], row["indicator"]), []).append(EVIDENCE_ADAPTER.validate_python(row["payload"]))
    return Snapshot(cells, grids, sources, sensors)


def grid_ids_in_bbox(conn, bbox):
    """GIST-backed equivalent of cached grid-ID filtering for operational queries."""
    return [r["grid_id"] for r in conn.execute("""SELECT grid_id FROM public.grid_cells
        WHERE geom && ST_MakeEnvelope(%s,%s,%s,%s,4326)
          AND ST_Intersects(geom,ST_MakeEnvelope(%s,%s,%s,%s,4326)) ORDER BY grid_id""", (*bbox, *bbox))]
