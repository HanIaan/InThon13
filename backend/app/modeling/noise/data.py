"""Data loading for the W4 noise model comparison.

Reads only the processed S-DoT contract CSVs (``data/processed/``) and the cached
external measurement files (``data/raw/external/``). No network, no database.

Target definition (per ``docs/data-dictionary.md`` and ``services/precompute.py``):
each sensor's mean noise (dB) in a ``time_slot`` computed from hourly rows whose
quality flags do not exclude the *noise* indicator. ``noise_*`` tokens drop only
noise; ``light_*`` tokens apply only to illuminance; any other token
(``no_valid_location``, ``duplicate_merged``, unknown) drops the whole row.

External networks (KECO, Seoul road traffic noise) are **independent validation
only**. They are never training data: S-DoT ``AVG_NIS`` has not been confirmed to
be an energy (Leq) average, so values are not summed or merged across sources.
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from app.contracts.constants import TIME_SLOT_HOURS, TimeSlot, hour_to_time_slot

KST = "Asia/Seoul"

# ISO-8601 with an explicit +09:00 offset in the processed CSV.
SDOT_TIME_COLUMN = "observed_at"


def excluded_indicators(quality_flag: str | None) -> set[str]:
    """Indicators excluded by a ``;``-separated quality_flag.

    Mirrors ``services.precompute.flagged_indicators`` (tested for equality).
    """
    excluded: set[str] = set()
    # Blank flags are read as NaN by pandas; a non-string flag carries no tokens.
    flag = quality_flag if isinstance(quality_flag, str) else ""
    for token in (t.strip() for t in flag.split(";")):
        if not token:
            continue
        prefix = token.split("_", 1)[0]
        excluded |= {prefix} if prefix in ("noise", "light") else {"noise", "light"}
    return excluded


def noise_is_usable(quality_flag: str | None, noise_db: float | None) -> bool:
    """True when the row's noise value survives the quality flags."""
    if noise_db is None or (isinstance(noise_db, float) and np.isnan(noise_db)):
        return False
    if noise_db < 0:
        return False
    return "noise" not in excluded_indicators(quality_flag)


def load_sdot_slot_means(processed_dir: str | Path) -> pd.DataFrame:
    """Sensor x time_slot mean noise rows.

    Columns: sensor_id, lng, lat, slot, noise_db, n_obs (usable hourly values).
    Sensors without any usable noise value in a slot are absent for that slot.
    """
    processed_dir = Path(processed_dir)
    sensors = pd.read_csv(processed_dir / "sdot_sensors.csv")
    observations = pd.read_csv(processed_dir / "sdot_observations.csv")

    sensors = sensors.dropna(subset=["lng", "lat"])
    observations = observations[observations["sensor_id"].isin(sensors["sensor_id"])]

    stamps = pd.to_datetime(observations[SDOT_TIME_COLUMN], utc=True).dt.tz_convert(KST)
    observations = observations.assign(
        hour=stamps.dt.hour,
        slot=stamps.dt.hour.map(lambda h: hour_to_time_slot(int(h)).value),
    )
    usable = np.fromiter(
        (noise_is_usable(q, v) for q, v in zip(observations["quality_flag"], observations["noise_db"])),
        dtype=bool,
        count=len(observations),
    )
    usable_rows = observations.loc[usable]

    grouped = (
        usable_rows.groupby(["sensor_id", "slot"], observed=True)["noise_db"]
        .agg(noise_db="mean", n_obs="count")
        .reset_index()
    )
    out = grouped.merge(
        sensors[["sensor_id", "lng", "lat", "name", "gu_name"]], on="sensor_id", how="inner"
    )
    return out.sort_values(["sensor_id", "slot"]).reset_index(drop=True)


def load_sdot_sensor_means(processed_dir: str | Path) -> pd.DataFrame:
    """Sensor-level mean noise over every usable hourly row (for reporting only)."""
    processed_dir = Path(processed_dir)
    sensors = pd.read_csv(processed_dir / "sdot_sensors.csv")
    observations = pd.read_csv(processed_dir / "sdot_observations.csv")
    sensors = sensors.dropna(subset=["lng", "lat"])
    observations = observations[observations["sensor_id"].isin(sensors["sensor_id"])]
    usable = np.fromiter(
        (noise_is_usable(q, v) for q, v in zip(observations["quality_flag"], observations["noise_db"])),
        dtype=bool,
        count=len(observations),
    )
    grouped = (
        observations.loc[usable].groupby("sensor_id", observed=True)["noise_db"]
        .agg(noise_db="mean", n_obs="count")
        .reset_index()
    )
    return grouped.merge(sensors[["sensor_id", "lng", "lat"]], on="sensor_id", how="inner")


# ---- KECO automatic network (data.go.kr 3042013) -----------------------------
# Seoul roadside sites only; coordinates come from the separate site file
# (data.go.kr 15093409). 2024 daily values, unit dB (Leq assumed but not
# verified against S-DoT: used for rank comparison only).
KECO_CITY = "서울특별시"


def load_keco_auto(
    auto_path: str | Path, sites_path: str | Path, city: str = KECO_CITY
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(daily rows, site summary) for one city's roadside automatic stations.

    The summary keeps only sites that have both a coordinate and measurements.
    """
    auto = pd.read_csv(auto_path, encoding="utf-8-sig")
    auto = auto[auto["도시"] == city].copy()
    auto["측정일"] = pd.to_datetime(auto["측정일"], errors="coerce")
    auto["소음도"] = pd.to_numeric(auto["소음도"], errors="coerce")
    auto = auto.dropna(subset=["측정일", "소음도"])

    sites = pd.read_csv(sites_path, encoding="utf-8-sig")
    sites = sites[sites["주소"].fillna("").str.contains("서울")].copy()
    sites["위도"] = pd.to_numeric(sites["위도"], errors="coerce")
    sites["경도"] = pd.to_numeric(sites["경도"], errors="coerce")
    sites = sites.dropna(subset=["위도", "경도"])
    sites = sites.drop_duplicates(subset=["측정지점"], keep="first")

    summary = (
        auto.groupby("측정지점", observed=True)["소음도"]
        .agg(measured_db="mean", n_days="count")
        .reset_index()
        .rename(columns={"측정지점": "site"})
        .merge(
            sites[["측정지점", "위도", "경도", "주소", "용도구분"]]
            .rename(columns={"측정지점": "site", "위도": "lat", "경도": "lng", "주소": "address"}),
            on="site",
            how="inner",
        )
    )
    return auto, summary.sort_values("site").reset_index(drop=True)


# ---- Seoul road traffic noise (OA-15473) -------------------------------------
# Four fixed roadside stations. The published workbooks carry **no coordinates**
# and no authoritative coordinate source was confirmed, so they are excluded from
# spatial validation (see docs/research/noise-model-evaluation.md).
SEOUL_ROAD_SITES = ("시청", "성수", "신촌", "신사")
SEOUL_ROAD_SITE_COORDS: dict[str, tuple[float, float]] = {}

# Sheet name prefix -> canonical site name (2026 H1 workbook uses 시청/성수/신촌/신사).
_SEOUL_SHEET_ALIASES = {"시청": "시청", "성수": "성수", "신촌": "신촌", "신사": "신사"}
_HOUR_COLUMNS = list(range(2, 26))  # workbook columns 2..25 hold hours 1..24


def _parse_seoul_month(workbook_bytes: bytes, month: str) -> list[dict]:
    rows: list[dict] = []
    book = pd.ExcelFile(BytesIO(workbook_bytes), engine="openpyxl")
    for sheet in book.sheet_names:
        site = next((v for k, v in _SEOUL_SHEET_ALIASES.items() if sheet.startswith(k)), None)
        if site is None:
            continue
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        header_row = next(
            (i for i in range(min(12, len(raw))) if str(raw.iloc[i, 1]).strip() == "측정일\\시간"),
            None,
        )
        if header_row is None:
            continue
        for i in range(header_row + 1, len(raw)):
            day = pd.to_datetime(raw.iloc[i, 1], errors="coerce")
            if pd.isna(day):
                continue
            for col in _HOUR_COLUMNS:
                value = pd.to_numeric(raw.iloc[i, col], errors="coerce")
                if pd.isna(value):
                    continue
                hour = int(raw.iloc[header_row, col]) % 24  # hour 24 == midnight
                rows.append(
                    dict(site=site, month=month, timestamp=day + pd.Timedelta(hours=hour),
                         hour=hour, leq=float(value))
                )
    return rows


def load_seoul_road_slots(zip_path: str | Path) -> pd.DataFrame:
    """Hourly LEQ from the 2026 H1 monthly workbooks, aggregated per time slot.

    Columns: site, slot, leq_db (mean LEQ), n_obs. No coordinates are attached.
    """
    records: list[dict] = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in sorted(n for n in archive.namelist() if n.lower().endswith(".xlsx")):
            month = Path(name).stem
            records.extend(_parse_seoul_month(archive.read(name), month))
    if not records:
        return pd.DataFrame(columns=["site", "slot", "leq_db", "n_obs"])
    hourly = pd.DataFrame(records)
    hourly["slot"] = hourly["hour"].map(lambda h: hour_to_time_slot(int(h)).value)
    return (
        hourly.groupby(["site", "slot"], observed=True)["leq"]
        .agg(leq_db="mean", n_obs="count")
        .reset_index()
    )


SLOT_ORDER = [slot.value for slot in TimeSlot]
SLOT_HOURS = {slot.value: TIME_SLOT_HOURS[slot] for slot in TimeSlot}
