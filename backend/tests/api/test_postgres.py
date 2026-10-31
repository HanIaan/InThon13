"""Opt-in real PostGIS roundtrip. Everything, including DDL, is rolled back.

RUN_NETWORK_TESTS=1 DATABASE_URL=<server-only DSN> python -m pytest tests/api/test_postgres.py -q
Use an isolated disposable DB; the publishing transaction temporarily locks engine
rows, even though it rolls back. Default tests never open a network connection.
"""
import pytest

from app.config import get_settings
from app.db.migrate import apply_migrations
from app.db.postgres import connect, grid_ids_in_bbox, publish_on_connection, read_on_connection
from app.db.snapshot import CELL_ADAPTER, SOURCE_ADAPTER
from app.services.precompute import build_snapshot
from tests.api.test_live_api import IX, IY, make_processed
from app.core import grid


@pytest.mark.network
def test_postgis_roundtrip_rls_and_idempotent_migration(tmp_path):
    database_url = get_settings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL is not configured")
    make_processed(tmp_path)
    snapshot, data = build_snapshot(tmp_path)
    conn = connect(database_url)
    try:
        apply_migrations(conn)
        assert apply_migrations(conn) == []
        conn.execute("SET LOCAL search_path TO pg_catalog")
        assert conn.execute("SELECT NULL::extensions.geometry(Point,4326) AS geom").fetchone()["geom"] is None
        conn.execute("SET LOCAL search_path TO public, extensions")
        publish_on_connection(conn, snapshot, data)
        loaded = read_on_connection(conn)
        expected = {(c.grid_id, c.time_slot): CELL_ADAPTER.dump_python(c, mode="json") for c in snapshot.cells}
        # PostgreSQL returns timestamps in its configured timezone. Normalize to KST.
        from tests.api.test_live_api import KST
        for cell in loaded.cells:
            for name in ("noise", "light", "crowd"):
                value = getattr(cell, name)
                for field in ("observed_at", "period_start", "period_end"):
                    stamp = getattr(value, field)
                    if stamp:
                        setattr(value, field, stamp.astimezone(KST))
                for evidence in value.evidence:
                    if evidence.observed_at:
                        evidence.observed_at = evidence.observed_at.astimezone(KST)
        assert {(c.grid_id, c.time_slot): CELL_ADAPTER.dump_python(c, mode="json") for c in loaded.cells} == expected
        assert len(loaded.grids) == 8 and len(loaded.sources) == 3
        lng, lat = grid.cell_center(IX, IY)
        assert grid.grid_id(IX, IY) in grid_ids_in_bbox(conn, (lng-.0001, lat-.0001, lng+.0001, lat+.0001))
        rows = list(conn.execute("""SELECT relname,relrowsecurity FROM pg_class
            WHERE relnamespace='public'::regnamespace AND relname IN
            ('sensors','sdot_observations','living_population','admin_dong','night_lights',
             'grid_cells','cell_indicators','source_metadata','sensor_summaries','engine_dataset')"""))
        assert len(rows) == 10 and all(r["relrowsecurity"] for r in rows)
        assert not conn.execute("SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='cell_indicators'").fetchall()
        # Exercise default-deny as a real non-owner role, including when Supabase
        # roles do not exist on a plain local PostgreSQL installation.
        conn.execute("CREATE ROLE w2_test_anon NOLOGIN")
        conn.execute("GRANT USAGE ON SCHEMA public TO w2_test_anon")
        conn.execute("GRANT SELECT ON public.cell_indicators TO w2_test_anon")
        conn.execute("SET LOCAL ROLE w2_test_anon")
        assert conn.execute("SELECT count(*) AS n FROM public.cell_indicators").fetchone()["n"] == 0
        conn.execute("RESET ROLE")
        # Replacement removes stale rows instead of merging generations.
        subset = type(snapshot)(snapshot.cells[:4], snapshot.grids[:1], snapshot.sources, snapshot.sensors)
        publish_on_connection(conn, subset, data)
        assert len(read_on_connection(conn).cells) == 4
    finally:
        conn.rollback()
        conn.close()
