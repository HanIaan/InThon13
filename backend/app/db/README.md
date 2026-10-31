# Live dataset operations

Run commands from `backend/` using the worktree's existing virtual environment.
No additional Python dependency is required.

## Local demonstration

Place ingestion outputs in `PROCESSED_DATA_DIR` (default `../data/processed`):

- `admin_dong_boundaries.geojson` is required; only intersecting Seoul grid cells are built.
- `sdot_sensors.csv` and `sdot_observations.csv` supply sensor data.
- `living_population.csv` and `nasa_night_lights.csv` are optional sources.

Use the field names/types from `app.contracts.data_interfaces`. Empty CSV fields
are null. Invalid/nonfinite rows fail the build; duplicate keys fail rather than
silently double counting. Flagged sensor rows, unlocated sensors and unusable NASA
quality flags are excluded from estimates. Missing sources stay missing.

```sh
../.venv/bin/python -m app.services.precompute --local-only
DATA_MODE=live ../.venv/bin/uvicorn app.main:app
```

Leave `DATABASE_URL` unset for local serving. Output is
`PROCESSED_DATA_DIR/cell_indicators.sqlite`, an atomic, versioned SQLite snapshot.
There is one row per `(grid_id, time_slot)`, including explicit missing indicators,
plus grid geometry, source metadata and per-sensor slot means for click evidence.
Precomputation rules and confidence equations are in `services/precompute.py`.
The API never reads the input CSV/GeoJSON files. Its process cache loads only the
snapshot; restart workers after publishing a replacement. A previously failed
initial load is retried, so publishing the first snapshot permits recovery.

A valid boundary with no usable observations yields all-null cells and degraded
health. A missing/corrupt snapshot gives 503 for map/inspect/recommendation requests;
health and data/status remain readable. Warm `/api/v1/health` during deployment
readiness checks to load the snapshot before interactive map traffic.

## PostGIS / Supabase

Configure `DATABASE_URL` only in the server environment or git-ignored
`backend/.env`. Use a direct PostgreSQL connection for an owner/BYPASSRLS account.
This implementation does not use Supabase REST or accept an API key as a DSN.

```sh
../.venv/bin/python -m app.db.migrate
../.venv/bin/python -m app.services.precompute
DATA_MODE=live ../.venv/bin/uvicorn app.main:app
```

The first migration requires permission to install PostGIS. Migrations have a
checksum history and an advisory lock. PostGIS must be installed in Supabase's `extensions` schema. Migration geometry
types and PostGIS functions are explicitly qualified and do not depend on
search_path. An existing installation in another schema must be reviewed by the
coordinator before migration; this script does not relocate existing extensions. All engine tables enable RLS and revoke
PUBLIC/anon/authenticated access; no public policies are created. The service_role
gets table privileges when that role exists. Plain direct connections must have
ownership/BYPASSRLS privileges; arbitrary DB users do not bypass RLS.

Publication atomically **replaces the complete engine-owned dataset**, including
raw processed records and metadata, in one transaction. It does not append to an
old generation. Other tables are untouched. Sensor observations with unknown
sensor IDs are excluded from the raw DB table; metadata explicitly counts input
rows and includes unusable/unlocated observations in its missing-rate calculation.
Local SQLite is published first; if DB publication fails, the new local snapshot
remains available. A configured but failing DB does not silently fall back to it:
unset DATABASE_URL explicitly to serve local files.

The provider loads a repeatable-read DB snapshot into a process cache keyed by
(grid_id, slot). This avoids repeated DB round trips for panning. GIST indexes on
WGS84/projected grid geometry and a slot/grid B-tree also support direct spatial
and operational queries (`grid_ids_in_bbox`). DB connection/statement timeouts are
5/30 seconds; driver messages are not exposed in HTTP responses or CLI errors.

Source metadata reports input record counts, invalid/missing fractions, usable
spatial/temporal bounds, input pixel resolutions and snapshot load time. It does
not claim an upstream update frequency that has not been verified.

## Verification

```sh
../.venv/bin/python -m pytest -q
../.venv/bin/python -m pytest tests/api/test_live_performance.py -q -s
RUN_NETWORK_TESTS=1 ../.venv/bin/python -m pytest tests/api/test_postgres.py -q
```

The last command additionally needs DATABASE_URL pointing at a **disposable test
DB** with PostGIS and migration/CREATE ROLE privileges. It exercises schema,
roundtrip, replacement, spatial lookup, and RLS using an unprivileged role. All
DDL/data/role changes are rolled back, but the test temporarily locks engine rows;
do not run it against a serving production dataset. Default tests skip network
checks even if DATABASE_URL exists. The performance fixture is synthetic test
input, stored only in pytest's temporary directory, and is never demo data.
