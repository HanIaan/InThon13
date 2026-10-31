"""S-DoT 환경정보 (data.seoul.go.kr OA-15969) -> sdot_sensors.csv, sdot_observations.csv.

Verified source facts (2026-10-31):
- Values are already **hourly aggregates** (min/avg/max per hour) published once per hour per
  sensor with ``MSRMT_HR = YYYY-MM-DD_HH:07:00``. We use ``AVG_NIS`` (dB) and ``AVG_INILLU`` (lux)
  and set ``observed_at`` to the start of that hour (minutes floored, Asia/Seoul).
- Weekly CSV files (Mon-Sun, cp949, no key) are published with ~1-2 weeks delay; the Open API
  (``IotVdata017``, key required) only serves the last month. Older years are yearly zips.
- Observation rows carry only an English 자치구/행정동 text which is not reliable (a ``Jung-gu``
  API filter returned a 동대문구 dong). Location comes from the separate file
  "S-DoT 환경정보 설치 위치정보.xlsx" (serial -> address, WGS84 lat/lng, 좌표구분 W84), and the
  행정동 is derived by point-in-polygon on the 2016 boundaries (same code system as 생활인구).

Quality flags (``quality_flag``; ``;``-joined tokens, ``None`` = no issue). Values are never
removed or altered by a flag:
- ``noise_out_of_range``: AVG noise outside [NOISE_MIN_DB, NOISE_MAX_DB]
- ``light_out_of_range``: AVG lux outside [LUX_MIN, LUX_MAX]
- ``noise_inconsistent_minmax`` / ``light_inconsistent_minmax``: not MIN <= AVG <= MAX
- ``noise_stuck_value``: identical AVG noise for >= NOISE_STUCK_HOURS consecutive hours
- ``light_stuck_value``: identical AVG lux for >= LUX_STUCK_HOURS consecutive hours (a lamp-lit
  night legitimately stays constant ~11 h, so the lux threshold spans a full day)
- ``duplicate_merged``: several differing rows for one sensor-hour were averaged
- ``no_valid_location``: the sensor has no valid Seoul coordinate in the location file
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.contracts.constants import DataStatus, SourceId
from app.contracts.data_interfaces import SensorLocation, SensorObservation

from .admin_dong import DongIndex
from .client import DatasetFile, SeoulOpenDataClient
from .common import (
    KST,
    floor_hour,
    log,
    parse_float,
    parse_kst_datetime,
    processed_dir,
    raw_dir,
    read_xlsx_rows,
    valid_seoul_point,
    write_records_csv,
)

INF_ID = "OA-15969"
API_SERVICE = "IotVdata017"
LOCATION_FILE_SEQ = "100000001"
LOCATION_FILENAME = "sdot_location.xlsx"
SENSORS_FILE = "sdot_sensors.csv"
OBSERVATIONS_FILE = "sdot_observations.csv"

NOISE_MIN_DB, NOISE_MAX_DB = 20.0, 140.0
LUX_MIN, LUX_MAX = 0.0, 200_000.0
NOISE_STUCK_HOURS = 12
LUX_STUCK_HOURS = 24

# Open API output order == weekly CSV column order (the CSV has Korean headers and up to
# 6 trailing empty fields per data row).
API_FIELDS = [
    "MDL_NO", "SN", "MSRMT_HR", "RGN", "CGG", "DONG",
    "MAX_TP", "AVG_TP", "MIN_TP", "MAX_HUM", "AVG_HUM", "MIN_HUM",
    "MAX_WSPD", "AVG_WSPD", "MIN_WSPD", "MAX_WD", "AVG_WD", "MIN_WD",
    "MAX_INILLU", "AVG_INILLU", "MIN_INILLU", "MAX_UV", "AVG_UV", "MIN_UV",
    "MAX_NIS", "AVG_NIS", "MIN_NIS",
    "MAX_VIBR_XCRD", "AVG_VIBR_XCRD", "MIN_VIBR_XCRD", "MAX_VIBR_YCRD", "AVG_VIBR_YCRD", "MIN_VIBR_YCRD",
    "MAX_VIBR_ZCRD", "AVG_VIBR_ZCRD", "MIN_VIBR_ZCRD", "MAX_GT", "AVG_GT", "MIN_GT",
    "MAX_NTDX", "AVG_NTDX", "MIN_NTDX", "MAX_CBMX", "AVG_CBMX", "MIN_CBMX",
    "MAX_SO", "AVG_SO", "MIN_SO", "MAX_NH3", "AVG_NH3", "MIN_NH3",
    "MAX_H2S", "AVG_H2S", "MIN_H2S", "MAX_OZON", "AVG_OZON", "MIN_OZON", "REG_DT",
]
# Header labels of the columns we rely on, to verify the positional mapping of weekly CSVs.
_CSV_HEADER_CHECK = {1: "시리얼", 2: "측정시간", 18: "조도 최대", 19: "조도 평균", 20: "조도 최소",
                     24: "소음 최대", 25: "소음 평균", 26: "소음 최소"}

GU_EN = {
    "Jongno-gu": "종로구", "Jung-gu": "중구", "Yongsan-gu": "용산구", "Seongdong-gu": "성동구",
    "Gwangjin-gu": "광진구", "Dongdaemun-gu": "동대문구", "Jungnang-gu": "중랑구", "Seongbuk-gu": "성북구",
    "Gangbuk-gu": "강북구", "Dobong-gu": "도봉구", "Nowon-gu": "노원구", "Eunpyeong-gu": "은평구",
    "Seodaemun-gu": "서대문구", "Mapo-gu": "마포구", "Yangcheon-gu": "양천구", "Gangseo-gu": "강서구",
    "Guro-gu": "구로구", "Geumcheon-gu": "금천구", "Yeongdeungpo-gu": "영등포구", "Dongjak-gu": "동작구",
    "Gwanak-gu": "관악구", "Seocho-gu": "서초구", "Gangnam-gu": "강남구", "Songpa-gu": "송파구",
    "Gangdong-gu": "강동구",
}

_WEEKLY_RE = re.compile(r"S-DoT_NATURE_(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.csv$")


# ------------------------------------------------------------------ sensor locations

@dataclass(frozen=True)
class RawSensorLocation:
    sensor_id: str
    address: str | None
    coord_code: str | None
    lat: float | None
    lng: float | None


def parse_location_rows(rows: list[list[str | None]]) -> list[RawSensorLocation]:
    """Rows of the 설치 위치정보 sheet: No | 모델 시리얼(*) | 주소 | 좌표 구분코드 | 위도 | 경도 | ..."""
    if not rows:
        return []
    header = [(h or "").strip() for h in rows[0]]

    def col(prefix: str) -> int:
        for i, h in enumerate(header):
            if h.startswith(prefix):
                return i
        raise ValueError(f"location sheet: column '{prefix}' not found in {header}")

    i_sn, i_addr, i_code, i_lat, i_lng = col("모델 시리얼"), col("주소"), col("좌표 구분"), col("위도"), col("경도")
    out: list[RawSensorLocation] = []
    for r in rows[1:]:
        get = lambda i: (r[i].strip() if i < len(r) and r[i] is not None else None)  # noqa: E731
        sn = get(i_sn)
        if not sn:
            continue
        out.append(RawSensorLocation(sn, get(i_addr) or None, get(i_code), parse_float(get(i_lat)), parse_float(get(i_lng))))
    return out


def _gu_from_address(address: str | None) -> str | None:
    if not address:
        return None
    for tok in address.split():
        if tok.endswith("구") and tok != "서울특별시":
            return tok
    return None


def build_sensor_locations(raw: Iterable[RawSensorLocation], dong_index: DongIndex | None) -> tuple[list[SensorLocation], list[RawSensorLocation]]:
    """Valid sensors and the rejected ones (invalid / outside-Seoul coordinates, duplicates).

    location_status is ``observed``: coordinates are published by the provider (좌표구분 W84);
    we do not geocode. A sensor whose published coordinate is not a WGS84 point in Seoul is
    rejected rather than guessed.
    """
    sensors: dict[str, SensorLocation] = {}
    rejected: list[RawSensorLocation] = []
    for r in raw:
        if r.sensor_id in sensors or (r.coord_code and r.coord_code.upper() != "W84") or not valid_seoul_point(r.lng, r.lat):
            rejected.append(r)
            continue
        dong = dong_index.lookup(r.lng, r.lat) if dong_index else None
        sensors[r.sensor_id] = SensorLocation(
            sensor_id=r.sensor_id,
            source=SourceId.sdot,
            lng=float(r.lng),
            lat=float(r.lat),
            name=r.address,
            gu_name=dong.gu_name if dong else _gu_from_address(r.address),
            dong_code=dong.dong_code if dong else None,
            installed_at=None,  # not published
            location_status=DataStatus.observed,
        )
    return sorted(sensors.values(), key=lambda s: s.sensor_id), rejected


# ------------------------------------------------------------------ observations: parsing

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp949", errors="replace")


def iter_weekly_csv(stream: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Weekly CSV (Korean header) -> dicts keyed by Open API field names."""
    reader = csv.reader(stream)
    header = next(reader, None)
    if header is None:
        return
    header = [h.strip().lstrip("﻿") for h in header]
    if header and header[0] in API_FIELDS:  # already API-named (e.g. sheet export)
        names = header
    else:
        for i, label in _CSV_HEADER_CHECK.items():
            if i >= len(header) or not header[i].startswith(label):
                raise ValueError(f"unexpected S-DoT CSV header at column {i}: {header[i:i + 1]} (expected {label})")
        names = API_FIELDS
    for row in reader:
        if not row:
            continue
        yield {k: (row[i] if i < len(row) else "") for i, k in enumerate(names)}


def read_weekly_csv(path: Path) -> Iterator[dict[str, Any]]:
    yield from iter_weekly_csv(io.StringIO(_decode(path.read_bytes()), newline=""))


@dataclass
class _Reading:
    sensor_id: str
    hour: datetime
    noise: float | None
    noise_min: float | None
    noise_max: float | None
    lux: float | None
    lux_min: float | None
    lux_max: float | None


def parse_reading(row: dict[str, Any]) -> _Reading | None:
    sn = str(row.get("SN") or "").strip()
    ts = parse_kst_datetime(row.get("MSRMT_HR"))
    if not sn or ts is None:
        return None
    f = lambda k: parse_float(row.get(k))  # noqa: E731
    return _Reading(sn, floor_hour(ts), f("AVG_NIS"), f("MIN_NIS"), f("MAX_NIS"), f("AVG_INILLU"), f("MIN_INILLU"), f("MAX_INILLU"))


# ------------------------------------------------------------------ observations: aggregation & QC

def _range_flags(r: _Reading) -> set[str]:
    flags: set[str] = set()
    if r.noise is not None and not (NOISE_MIN_DB <= r.noise <= NOISE_MAX_DB):
        flags.add("noise_out_of_range")
    if r.lux is not None and not (LUX_MIN <= r.lux <= LUX_MAX):
        flags.add("light_out_of_range")
    for name, lo, avg, hi in (("noise", r.noise_min, r.noise, r.noise_max), ("light", r.lux_min, r.lux, r.lux_max)):
        if avg is not None and ((lo is not None and lo > avg) or (hi is not None and hi < avg)):
            flags.add(f"{name}_inconsistent_minmax")
    return flags


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _stuck_hours(series: list[tuple[datetime, float | None]], min_hours: int) -> set[datetime]:
    """Hours belonging to runs of >= min_hours consecutive hours with an identical value."""
    stuck: set[datetime] = set()
    run: list[datetime] = []
    prev_t: datetime | None = None
    prev_v: float | None = None
    for t, v in series + [(None, None)]:  # sentinel flushes the last run
        continues = v is not None and prev_v is not None and v == prev_v and prev_t is not None and t - prev_t == timedelta(hours=1)
        if continues:
            run.append(t)
        else:
            if len(run) >= min_hours:
                stuck.update(run)
            run = [t] if v is not None else []
        prev_t, prev_v = t, v
    return stuck


@dataclass
class ObservationStats:
    input_rows: int = 0
    dropped_unparsable: int = 0
    dropped_out_of_period: int = 0
    duplicate_rows: int = 0
    rows: int = 0
    sensors: int = 0
    noise_missing: int = 0
    lux_missing: int = 0
    flagged: int = 0
    period_start: datetime | None = None
    period_end: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["noise_missing_rate"] = round(self.noise_missing / self.rows, 4) if self.rows else None
        d["lux_missing_rate"] = round(self.lux_missing / self.rows, 4) if self.rows else None
        for k in ("period_start", "period_end"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def build_observations(
    rows: Iterable[dict[str, Any]],
    start: date | None = None,
    end: date | None = None,
    located_sensor_ids: set[str] | None = None,
) -> tuple[list[SensorObservation], ObservationStats]:
    """Raw S-DoT rows -> one SensorObservation per sensor-hour within [start, end] (KST dates)."""
    st = ObservationStats()
    groups: dict[tuple[str, datetime], list[_Reading]] = defaultdict(list)
    for row in rows:
        st.input_rows += 1
        r = parse_reading(row)
        if r is None:
            st.dropped_unparsable += 1
            continue
        d = r.hour.date()
        if (start and d < start) or (end and d > end):
            st.dropped_out_of_period += 1
            continue
        groups[(r.sensor_id, r.hour)].append(r)

    merged: dict[tuple[str, datetime], tuple[_Reading, int, set[str]]] = {}
    for key, rs in groups.items():
        flags: set[str] = set()
        uniq = {(x.noise, x.noise_min, x.noise_max, x.lux, x.lux_min, x.lux_max): x for x in rs}
        st.duplicate_rows += len(rs) - 1
        if len(uniq) == 1:
            r, count = rs[0], 1  # exact duplicates of one reading
        else:
            vals = list(uniq.values())
            r = _Reading(key[0], key[1],
                         _mean([x.noise for x in vals]), min((x.noise_min for x in vals if x.noise_min is not None), default=None),
                         max((x.noise_max for x in vals if x.noise_max is not None), default=None),
                         _mean([x.lux for x in vals]), min((x.lux_min for x in vals if x.lux_min is not None), default=None),
                         max((x.lux_max for x in vals if x.lux_max is not None), default=None))
            count = len(vals)
            flags.add("duplicate_merged")
        flags |= _range_flags(r)
        if located_sensor_ids is not None and r.sensor_id not in located_sensor_ids:
            flags.add("no_valid_location")
        merged[key] = (r, count, flags)

    by_sensor: dict[str, list[tuple[datetime, _Reading]]] = defaultdict(list)
    for (sn, hour), (r, _, _) in merged.items():
        by_sensor[sn].append((hour, r))
    for sn, series in by_sensor.items():
        series.sort(key=lambda x: x[0])
        for hour in _stuck_hours([(h, r.noise) for h, r in series], NOISE_STUCK_HOURS):
            merged[(sn, hour)][2].add("noise_stuck_value")
        for hour in _stuck_hours([(h, r.lux) for h, r in series], LUX_STUCK_HOURS):
            merged[(sn, hour)][2].add("light_stuck_value")

    out: list[SensorObservation] = []
    for (sn, hour) in sorted(merged):
        r, count, flags = merged[(sn, hour)]
        out.append(SensorObservation(
            sensor_id=sn,
            observed_at=hour,
            noise_db=r.noise,
            illuminance_lux=r.lux,
            sample_count=count,
            quality_flag=";".join(sorted(flags)) or None,
        ))
        st.noise_missing += r.noise is None
        st.lux_missing += r.lux is None
        st.flagged += bool(flags)
    st.rows = len(out)
    st.sensors = len(by_sensor)
    if out:
        st.period_start = min(o.observed_at for o in out)
        st.period_end = max(o.observed_at for o in out)
    return out, st


# ------------------------------------------------------------------ fetching

def weekly_file_period(filename: str) -> tuple[date, date] | None:
    m = _WEEKLY_RE.search(filename)
    if not m:
        return None
    y, m1, d1, m2, d2 = map(int, m.groups())
    s = date(y, m1, d1)
    e = date(y + (1 if m2 < m1 else 0), m2, d2)
    return s, e


def select_weekly_files(files: list[DatasetFile], start: date, end: date) -> list[tuple[DatasetFile, date, date]]:
    out = []
    for f in files:
        p = weekly_file_period(f.filename)
        if p and p[0] <= end and p[1] >= start:
            out.append((f, *p))
    return sorted(out, key=lambda x: x[1])


def latest_weekly_period(files: list[DatasetFile]) -> tuple[date, date] | None:
    periods = [p for f in files if (p := weekly_file_period(f.filename))]
    return max(periods) if periods else None


def _api_rows_for_days(client: SeoulOpenDataClient, days: list[date]) -> Iterator[dict[str, Any]]:
    """REG_DTTM filter is the registration date; query each 자치구 for each day (+1 for late rows)."""
    query_days = sorted(set(days) | {d + timedelta(days=1) for d in days})
    for d in query_days:
        for gu in GU_EN:
            yield from client.api_rows(API_SERVICE, gu, d.isoformat())


def fetch_location_file(client: SeoulOpenDataClient) -> Path:
    return client.download_dataset_file(INF_ID, LOCATION_FILE_SEQ, raw_dir() / "sdot" / LOCATION_FILENAME)


def run(
    start: date | None,
    end: date | None,
    *,
    client: SeoulOpenDataClient,
    dong_index: DongIndex | None,
    out_dir: Path | None = None,
    use_api: bool | None = None,
) -> dict[str, Any]:
    out_dir = out_dir or processed_dir()
    files = client.list_dataset_files(INF_ID)
    if start is None or end is None:
        latest = latest_weekly_period(files)
        if latest is None:
            raise RuntimeError("no weekly S-DoT files listed on the dataset page")
        start, end = start or latest[0], end or latest[1]
        log.info("S-DoT period defaulted to latest weekly file: %s..%s", start, end)

    # sensors
    raw_locs = parse_location_rows(read_xlsx_rows(fetch_location_file(client)))
    sensors, rejected = build_sensor_locations(raw_locs, dong_index)
    for r in rejected:
        log.warning("sensor %s rejected: coord_code=%s lat=%s lng=%s", r.sensor_id, r.coord_code, r.lat, r.lng)
    n_sensors = write_records_csv(out_dir / SENSORS_FILE, SensorLocation, sensors)

    # observations
    selected = select_weekly_files(files, start, end)
    covered: set[date] = set()
    sources: list[Iterable[dict[str, Any]]] = []
    for f, ps, pe in selected:
        path = client.download_dataset_file(INF_ID, f.seq, raw_dir() / "sdot" / f.filename)
        sources.append(read_weekly_csv(path))
        covered |= {ps + timedelta(days=i) for i in range((pe - ps).days + 1)}
    wanted = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    gap = sorted(wanted - covered)
    api_used = False
    if gap:
        if use_api is not False and not client.is_sample:
            log.info("fetching %d day(s) not in weekly files from Open API: %s..%s", len(gap), gap[0], gap[-1])
            sources.append(_api_rows_for_days(client, gap))
            api_used = True
        else:
            log.warning("no weekly file and no SEOUL_OPEN_API_KEY for %d day(s): %s..%s", len(gap), gap[0], gap[-1])

    def all_rows():
        for s in sources:
            yield from s

    obs, stats = build_observations(all_rows(), start, end, {s.sensor_id for s in sensors})
    write_records_csv(out_dir / OBSERVATIONS_FILE, SensorObservation, obs)
    observed_days = {o.observed_at.date() for o in obs}
    result = {
        "sensors_file": str(out_dir / SENSORS_FILE),
        "observations_file": str(out_dir / OBSERVATIONS_FILE),
        "requested": [start.isoformat(), end.isoformat()],
        "weekly_files": [f.filename for f, _, _ in selected],
        "api_used": api_used,
        "uncovered_days": [d.isoformat() for d in sorted(wanted - observed_days)],
        "sensor_locations": n_sensors,
        "sensor_locations_rejected": [r.sensor_id for r in rejected],
        "sensors_with_dong": sum(1 for s in sensors if s.dong_code),
        **stats.as_dict(),
    }
    log.info("S-DoT: %s", result)
    return result


__all__ = [
    "API_FIELDS", "build_observations", "build_sensor_locations", "iter_weekly_csv", "parse_location_rows",
    "parse_reading", "read_weekly_csv", "run", "select_weekly_files", "weekly_file_period", "KST",
]
