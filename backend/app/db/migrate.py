"""Apply checksum-locked engine SQL migrations with DATABASE_URL from backend/.env.

Usage (backend/): ../.venv/bin/python -m app.db.migrate
Requires PostGIS extension-install privileges on first run; no Supabase API keys
are needed. A direct postgres/service role with BYPASSRLS is required at runtime.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import get_settings
from app.db.postgres import LOCK_ID, connect

# db/migrate.py -> app -> backend -> repository
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def apply_migrations(conn, directory=None):
    directory = Path(directory) if directory else MIGRATIONS
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    conn.execute("""CREATE TABLE IF NOT EXISTS public.engine_schema_migrations (
        name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
    conn.execute("ALTER TABLE public.engine_schema_migrations ENABLE ROW LEVEL SECURITY")
    conn.execute("REVOKE ALL ON public.engine_schema_migrations FROM PUBLIC")
    conn.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
            REVOKE ALL ON public.engine_schema_migrations FROM anon;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
            REVOKE ALL ON public.engine_schema_migrations FROM authenticated;
        END IF;
    END $$""")
    applied = []
    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise ValueError("no migrations found")
    for path in paths:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        old = conn.execute("SELECT checksum FROM public.engine_schema_migrations WHERE name=%s", (path.name,)).fetchone()
        if old:
            if old["checksum"] != checksum:
                raise ValueError("applied migration checksum mismatch")
            continue
        conn.execute(sql)
        conn.execute("INSERT INTO public.engine_schema_migrations (name,checksum) VALUES (%s,%s)", (path.name, checksum))
        applied.append(path.name)
    return applied


def main():
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required (server-only direct PostgreSQL connection).")
    try:
        with connect(settings.database_url) as conn:
            applied = apply_migrations(conn)
    except Exception:
        raise SystemExit("Migration failed. Check DB access, PostGIS privileges and migration history.") from None
    print(f"Applied {len(applied)} migration(s)")


if __name__ == "__main__":
    main()
