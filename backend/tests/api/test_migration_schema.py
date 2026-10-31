"""Offline guard for Supabase's extension schema; real resolution is opt-in DB QA."""
import re

from app.db.migrate import MIGRATIONS


def test_postgis_migration_is_independent_of_search_path():
    sql = (MIGRATIONS / "202610310001_live_engine.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;" in sql
    geometry_types = re.findall(r"[\w.]*geometry\s*\(", sql, re.IGNORECASE)
    assert geometry_types and all(t.lower().startswith("extensions.geometry") for t in geometry_types)
    spatial_functions = re.findall(r"[\w.]*ST_\w+\s*\(", sql, re.IGNORECASE)
    assert spatial_functions and all(f.lower().startswith("extensions.st_") for f in spatial_functions)
    assert not re.search(r"SET\s+(?:LOCAL\s+)?search_path", sql, re.IGNORECASE)
