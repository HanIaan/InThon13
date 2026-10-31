"""행정동 단위 서울 생활인구(내국인) (data.seoul.go.kr OA-14991) -> living_population.csv.

Verified source facts (2026-10-31):
- Monthly zip ``LOCAL_PEOPLE_DONG_YYYYMM.zip`` (UTF-8 BOM CSV, no key) and Open API
  ``SPOP_LOCAL_RESD_DONG`` (key; last 2 months; filters ``/{YYYYMMDD}/{HH}``).
- 424 행정동 x 24 hours per day, 2016 행정동 codes. ``TOT_LVPOP_CO`` is a statistical estimate
  (persons), decimals included. Attributes with <3 persons are masked as ``*``.
- **Production of the 행정동 series ended after 2026-07-31** (switch to the national 250 m grid);
  the last available month is 2026-07.

Rules:
- One record per (dong_code, stat_date, hour). Exact duplicates are dropped; conflicting
  duplicates keep the first row and are counted.
- ``hour`` is ``시간대구분`` 00..23 (Asia/Seoul). Values outside 0..23 are dropped.
- A masked/missing total (``*`` / empty) cannot be represented (``total_population`` is
  required) and the row is dropped and counted — never replaced by 0.
- ``area_km2`` is the area of the 2016 boundary polygon in EPSG:5179.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.contracts.data_interfaces import LivingPopulationRecord

from .admin_dong import AdminDong
from .client import DatasetFile, SeoulOpenDataClient
from .common import log, parse_float, processed_dir, raw_dir, write_records_csv

INF_ID = "OA-14991"
API_SERVICE = "SPOP_LOCAL_RESD_DONG"
OUTPUT_FILE = "living_population.csv"
LAST_PUBLISHED_DATE = date(2026, 7, 31)  # 행정동 series discontinued after this date

_KO_TO_API = {"기준일ID": "STDR_DE_ID", "시간대구분": "TMZON_PD_SE", "행정동코드": "ADSTRD_CODE_SE", "총생활인구수": "TOT_LVPOP_CO"}
_MONTHLY_RE = re.compile(r"LOCAL_PEOPLE_DONG_(\d{4})(\d{2})\.zip$")


def iter_population_csv(stream: Iterable[str]) -> Iterator[dict[str, Any]]:
    reader = csv.reader(stream)
    header = next(reader, None)
    if header is None:
        return
    names = [_KO_TO_API.get(h.strip().lstrip("﻿"), h.strip().lstrip("﻿")) for h in header]
    for need in ("STDR_DE_ID", "TMZON_PD_SE", "ADSTRD_CODE_SE", "TOT_LVPOP_CO"):
        if need not in names:
            raise ValueError(f"living population CSV: column {need} missing in header {header[:5]}")
    for row in reader:
        if row:
            yield dict(zip(names, row))


def read_monthly_zip(path: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.lower().endswith(".csv"):
                with z.open(name) as fh:
                    yield from iter_population_csv(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""))


def _parse_date(s: Any) -> date | None:
    s = str(s or "").strip()
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_hour(s: Any) -> int | None:
    s = str(s or "").strip()
    if not s.isdigit():
        return None
    h = int(s)
    return h if 0 <= h <= 23 else None


@dataclass
class PopulationStats:
    input_rows: int = 0
    dropped_unparsable: int = 0
    dropped_masked_total: int = 0
    dropped_out_of_period: int = 0
    duplicate_rows: int = 0
    conflicting_duplicates: int = 0
    rows: int = 0
    dongs: int = 0
    dongs_without_boundary: int = 0
    expected_rows: int = 0
    period_start: date | None = None
    period_end: date | None = None

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["missing_rate"] = round(1 - self.rows / self.expected_rows, 4) if self.expected_rows else None
        for k in ("period_start", "period_end"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def build_records(
    rows: Iterable[dict[str, Any]],
    start: date | None,
    end: date | None,
    dongs: dict[str, AdminDong] | None = None,
) -> tuple[list[LivingPopulationRecord], PopulationStats]:
    st = PopulationStats()
    seen: dict[tuple[str, date, int], float] = {}
    for row in rows:
        st.input_rows += 1
        code = str(row.get("ADSTRD_CODE_SE") or "").strip()
        d = _parse_date(row.get("STDR_DE_ID"))
        h = _parse_hour(row.get("TMZON_PD_SE"))
        if not re.fullmatch(r"\d{8}", code) or d is None or h is None:
            st.dropped_unparsable += 1
            continue
        if (start and d < start) or (end and d > end):
            st.dropped_out_of_period += 1
            continue
        total = parse_float(row.get("TOT_LVPOP_CO"))
        if total is None or total < 0:
            st.dropped_masked_total += 1
            continue
        key = (code, d, h)
        if key in seen:
            st.duplicate_rows += 1
            st.conflicting_duplicates += seen[key] != total
            continue
        seen[key] = total

    out: list[LivingPopulationRecord] = []
    missing_boundary: set[str] = set()
    for (code, d, h) in sorted(seen):
        dong = dongs.get(code) if dongs else None
        if dongs is not None and dong is None:
            missing_boundary.add(code)
        out.append(LivingPopulationRecord(
            dong_code=code,
            stat_date=d,
            hour=h,
            total_population=seen[(code, d, h)],
            dong_name=dong.dong_name if dong else None,
            area_km2=round(dong.area_km2, 4) if dong else None,
        ))
    st.rows = len(out)
    codes = {r.dong_code for r in out}
    days = {r.stat_date for r in out}
    st.dongs = len(codes)
    st.dongs_without_boundary = len(missing_boundary)
    if out:
        st.period_start, st.period_end = min(days), max(days)
        n_days = (st.period_end - st.period_start).days + 1
        st.expected_rows = len(codes | set(dongs or {})) * n_days * 24
    for c in sorted(missing_boundary):
        log.warning("living population dong %s has no boundary", c)
    return out, st


def monthly_files(files: list[DatasetFile]) -> dict[tuple[int, int], DatasetFile]:
    out = {}
    for f in files:
        m = _MONTHLY_RE.search(f.filename)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = f
    return out


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def default_period(files: list[DatasetFile], days: int = 7) -> tuple[date, date]:
    """Last `days` days of the most recent monthly file."""
    months = monthly_files(files)
    if not months:
        raise RuntimeError("no LOCAL_PEOPLE_DONG monthly files listed")
    y, m = max(months)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    end = nxt - timedelta(days=1)
    return end - timedelta(days=days - 1), end


def run(
    start: date | None,
    end: date | None,
    *,
    client: SeoulOpenDataClient,
    dongs: list[AdminDong] | None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or processed_dir()
    files = client.list_dataset_files(INF_ID)
    if start is None or end is None:
        start, end = default_period(files)
        log.info("living population period defaulted to %s..%s", start, end)
    if start > LAST_PUBLISHED_DATE:
        log.warning("행정동 생활인구 ends %s; requested %s..%s has no data", LAST_PUBLISHED_DATE, start, end)
    available = monthly_files(files)
    paths = []
    for ym in months_between(start, end):
        f = available.get(ym)
        if f is None:
            log.warning("no monthly living population file for %04d-%02d", *ym)
            continue
        paths.append(client.download_dataset_file(INF_ID, f.seq, raw_dir() / "population" / f.filename))

    def rows():
        for p in paths:
            yield from read_monthly_zip(p)

    by_code = {d.dong_code: d for d in dongs} if dongs is not None else None
    recs, st = build_records(rows(), start, end, by_code)
    write_records_csv(out_dir / OUTPUT_FILE, LivingPopulationRecord, recs)
    result = {"file": str(out_dir / OUTPUT_FILE), "requested": [start.isoformat(), end.isoformat()],
              "monthly_files": [p.name for p in paths], **st.as_dict()}
    log.info("living population: %s", result)
    return result
