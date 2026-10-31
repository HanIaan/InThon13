"""Versioned SQLite snapshot, published by atomic rename; API reads only this output.

Run precompute again and restart API workers to publish a new generation. No ingestion
or interpolation takes place at server startup or in request handlers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.data_interfaces import CellIndicators, EvidenceSensor, SourceInfo

SNAPSHOT_NAME = "cell_indicators.sqlite"
FORMAT_VERSION = 2  # 2: light = NASA only, per-indicator S-DoT quality flags
CELL_ADAPTER = TypeAdapter(CellIndicators)
SOURCE_ADAPTER = TypeAdapter(SourceInfo)
EVIDENCE_ADAPTER = TypeAdapter(EvidenceSensor)


@dataclass
class Snapshot:
    cells: list[CellIndicators]
    grids: list[dict]
    sources: list[SourceInfo]
    # (slot, indicator) -> all usable per-sensor hourly means for click evidence.
    sensors: dict[tuple[str, str], list[EvidenceSensor]]


def write_snapshot(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cell_indicators-", suffix=".sqlite", dir=path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as conn:
            conn.executescript("""
                CREATE TABLE manifest (version INTEGER NOT NULL);
                CREATE TABLE grid_cells (grid_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE cell_indicators (
                    grid_id TEXT NOT NULL, time_slot TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (time_slot, grid_id));
                CREATE TABLE source_metadata (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE sensor_summaries (
                    time_slot TEXT, indicator TEXT, sensor_id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY (time_slot, indicator, sensor_id));
            """)
            conn.execute("INSERT INTO manifest VALUES (?)", (FORMAT_VERSION,))
            conn.executemany("INSERT INTO grid_cells VALUES (?, ?)",
                             [(g["grid_id"], json.dumps(g)) for g in snapshot.grids])
            conn.executemany("INSERT INTO cell_indicators VALUES (?, ?, ?)",
                             [(c.grid_id, c.time_slot.value, CELL_ADAPTER.dump_json(c).decode()) for c in snapshot.cells])
            conn.executemany("INSERT INTO source_metadata VALUES (?, ?)",
                             [(s.source_id.value, SOURCE_ADAPTER.dump_json(s).decode()) for s in snapshot.sources])
            conn.executemany("INSERT INTO sensor_summaries VALUES (?, ?, ?, ?)",
                             [(slot, ind, e.sensor_id, EVIDENCE_ADAPTER.dump_json(e).decode())
                              for (slot, ind), values in snapshot.sensors.items() for e in values])
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_snapshot(path: Path) -> Snapshot:
    # mode=ro prevents silently creating an empty database when data are not prepared.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("SELECT version FROM manifest").fetchone() != (FORMAT_VERSION,):
            raise ValueError("unsupported snapshot version")
        cells = [CELL_ADAPTER.validate_json(r[0]) for r in conn.execute("SELECT payload FROM cell_indicators")]
        grids = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM grid_cells")]
        sources = [SOURCE_ADAPTER.validate_json(r[0]) for r in conn.execute("SELECT payload FROM source_metadata")]
        sensors = {}
        for slot, ind, payload in conn.execute("SELECT time_slot, indicator, payload FROM sensor_summaries"):
            sensors.setdefault((slot, ind), []).append(EVIDENCE_ADAPTER.validate_json(payload))
    return Snapshot(cells, grids, sources, sensors)
