"""Shared helpers for the Seoul ingestion pipeline: paths, time zone, parsing, CSV output.

Rules made explicit here (and tested in tests/ingestion/seoul):
- Time zone: every source timestamp is Seoul local time (KST, UTC+9, no DST). Parsed values
  become timezone-aware ``Asia/Seoul`` datetimes and are written as ISO-8601 with ``+09:00``.
- Missing values: empty strings, whitespace, ``*`` (비식별화), ``-`` and unparsable numbers
  become ``None`` -> empty CSV cell. Missing is never turned into 0.
- Coordinates: a point is valid only if it is finite and inside ``SEOUL_BBOX`` (WGS84).
"""
from __future__ import annotations

import csv
import dataclasses
import enum
import logging
import math
import re
import zipfile
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.contracts.constants import SEOUL_BBOX, TIMEZONE

log = logging.getLogger("ingestion.seoul")

KST = ZoneInfo(TIMEZONE)
BACKEND_DIR = Path(__file__).resolve().parents[3]

MISSING_TOKENS = frozenset({"", "*", "-", "null", "none", "nan", "n/a"})


# ------------------------------------------------------------------ paths

def processed_dir() -> Path:
    p = Path(get_settings().processed_data_dir)
    return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


def raw_dir() -> Path:
    """data/raw/seoul (sibling of the processed dir)."""
    return processed_dir().parent / "raw" / "seoul"


# ------------------------------------------------------------------ parsing

def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    s = str(value).strip().replace(",", "")
    if s.lower() in MISSING_TOKENS:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_kst_datetime(value: str) -> datetime | None:
    """Parse S-DoT style timestamps as Seoul local time.

    Accepts ``2026-10-12_00:07:00``, ``2026-10-12 00:07:00``, ``2026-10-12T00:07``,
    ``202610120007``. A value already carrying an offset is converted to KST.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in MISSING_TOKENS:
        return None
    s = s.replace("_", " ").replace("T", " ")
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})?", s)
    if m:
        y, mo, d, h, mi = m.groups()
        return datetime(int(y), int(mo), int(d), int(h), int(mi or 0), tzinfo=KST)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(KST).replace(minute=0, second=0, microsecond=0)


def valid_seoul_point(lng: float | None, lat: float | None) -> bool:
    if lng is None or lat is None or not (math.isfinite(lng) and math.isfinite(lat)):
        return False
    s = SEOUL_BBOX
    return s[0] <= lng <= s[2] and s[1] <= lat <= s[3]


# ------------------------------------------------------------------ CSV output

def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, enum.Enum):
        return str(v.value)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise ValueError(f"naive datetime in output: {v!r}")
        return v.astimezone(KST).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(round(v, 6)) if math.isfinite(v) else ""
    return str(v)


def field_names(record_type: type) -> list[str]:
    return [f.name for f in dataclasses.fields(record_type)]


def write_records_csv(path: Path, record_type: type, records: Iterable[Any]) -> int:
    """Write dataclass records; header == dataclass field names (contract). Returns row count."""
    cols = field_names(record_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for rec in records:
            if not isinstance(rec, record_type):
                raise TypeError(f"expected {record_type.__name__}, got {type(rec).__name__}")
            w.writerow([_cell(getattr(rec, c)) for c in cols])
            n += 1
    tmp.replace(path)
    return n


# ------------------------------------------------------------------ minimal xlsx reader

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_letters(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx_rows(path: Path, sheet: int = 1) -> list[list[str | None]]:
    """Read the first sheet of an .xlsx into a list of rows (stdlib only, values as text)."""
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t")) for si in root.findall(f"{_XLSX_NS}si")]
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    rows: list[list[str | None]] = []
    for r in root.iter(f"{_XLSX_NS}row"):
        cells: dict[int, str | None] = {}
        for c in r.findall(f"{_XLSX_NS}c"):
            t = c.get("t")
            v = c.find(f"{_XLSX_NS}v")
            val = v.text if v is not None else None
            if t == "s" and val is not None:
                val = shared[int(val)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{_XLSX_NS}t"))
            cells[_col_index(_col_letters(c.get("r", "A")))] = val
        if cells:
            rows.append([cells.get(i) for i in range(max(cells) + 1)])
    return rows
