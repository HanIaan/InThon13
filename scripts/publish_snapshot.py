"""Publish a prebuilt SQLite snapshot (and its processed inputs) to Supabase in one transaction.

    python scripts/publish_snapshot.py <backend dir> <processed dir> <.env.supabase>

The existing rows are replaced inside a single transaction (app.db.postgres.publish), so a
failure leaves Supabase as it was. DATABASE_URL is read from the env file into this process
only and never printed. To roll back, publish the previous snapshot the same way.
"""
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

backend, processed, env_file = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, backend)
from app.db.postgres import publish  # noqa: E402
from app.db.processed import load_processed  # noqa: E402
from app.db.snapshot import SNAPSHOT_NAME, read_snapshot  # noqa: E402

url = dotenv_values(env_file).get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL missing")
start = time.time()
snapshot = read_snapshot(processed / SNAPSHOT_NAME)
data = load_processed(processed)
print(f"loaded snapshot: {len(snapshot.cells)} cell-slots, {len(snapshot.grids)} grids ({time.time() - start:.0f}s)", flush=True)
try:
    publish(url, snapshot, data)
except Exception as exc:  # connection errors can carry credentials; print the type only
    sys.exit(f"publish failed: {type(exc).__name__}")
print(f"published in {time.time() - start:.0f}s", flush=True)
