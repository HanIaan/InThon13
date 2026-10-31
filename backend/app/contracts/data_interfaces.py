"""Data interfaces between ingestion, storage and the API engine. COORDINATOR-OWNED.

Flow:
    ingestion (Worker 1: Seoul, Worker 3: NASA)
        -> normalised records below, written to data/processed/<file> (see PROCESSED_FILES)
    engine/db (Worker 2)
        -> loads processed files into PostGIS, precomputes CellIndicators per (grid_id, time_slot)
    API
        -> reads CellIndicators via a DataProvider, applies user sensitivities per request

Rules:
- All timestamps are timezone-aware, Asia/Seoul.
- All coordinates are WGS84 lng/lat.
- Never fabricate measurements. Missing value -> None, not 0.
- `data_status` is "observed" for direct measurements, "estimated" for interpolation or
  areal disaggregation, "mock" for synthetic data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from .constants import DataStatus, SourceId, TimeSlot

# ---------------------------------------------------------------- ingestion outputs

# File name (under data/processed/) -> record type written there. CSV, UTF-8, header row,
# column names == dataclass field names, ISO-8601 datetimes with +09:00 offset.
PROCESSED_FILES = {
    "sdot_sensors.csv": "SensorLocation",
    "sdot_observations.csv": "SensorObservation",
    "living_population.csv": "LivingPopulationRecord",
    "admin_dong_boundaries.geojson": "AdminDong (GeoJSON FeatureCollection, properties: dong_code, dong_name, gu_name)",
    "nasa_night_lights.csv": "NightLightRecord",
}


@dataclass(frozen=True)
class SensorLocation:
    sensor_id: str          # S-DoT serial / model id as published
    source: SourceId        # SourceId.sdot
    lng: float
    lat: float
    name: str | None = None
    gu_name: str | None = None      # 자치구
    dong_code: str | None = None    # 행정동 코드 (8 digits, 행정안전부)
    installed_at: datetime | None = None
    location_status: DataStatus = DataStatus.observed  # "estimated" if geocoded from address


@dataclass(frozen=True)
class SensorObservation:
    """One sensor reading, aggregated to an hour if the source is sub-hourly."""
    sensor_id: str
    observed_at: datetime            # start of the hour, Asia/Seoul
    noise_db: float | None           # average sound level, dB
    illuminance_lux: float | None    # ambient light, lux
    sample_count: int = 1            # raw readings folded into this row
    quality_flag: str | None = None  # None = ok; otherwise e.g. "out_of_range", "stuck_value"


@dataclass(frozen=True)
class LivingPopulationRecord:
    """서울 생활인구 (내국인 기준). One row per 행정동 per date per hour."""
    dong_code: str
    stat_date: date
    hour: int                        # 0-23, Asia/Seoul
    total_population: float          # persons (published estimate)
    dong_name: str | None = None
    area_km2: float | None = None    # 행정동 면적, for density


@dataclass(frozen=True)
class NightLightRecord:
    """NASA Black Marble pixel (VNP46A2 / VNP46A3 / VNP46A4). Pixel centre in WGS84."""
    pixel_id: str                    # "<h><v>_<row>_<col>" in the source tile
    lng: float
    lat: float
    pixel_size_deg: float            # 15 arc-second ~= 0.004167
    product: str                     # e.g. "VNP46A2", "VNP46A3"
    period_start: date
    period_end: date
    radiance: float | None           # nW/cm²/sr, Gap-filled DNB BRDF-corrected NTL
    quality_flag: int | None = None  # Mandatory_Quality_Flag: 0 high, 1 high-gapfilled, 2 poor, 255 fill
    cloud_flag: int | None = None


# ---------------------------------------------------------------- engine <-> API

@dataclass
class EvidenceSensor:
    sensor_id: str
    source: SourceId
    lng: float
    lat: float
    distance_m: float
    raw_value: float | None
    raw_unit: str | None
    observed_at: datetime | None
    name: str | None = None


@dataclass
class IndicatorValue:
    """Pre-computed raw indicator for one cell and time slot (before user weighting)."""
    raw_value: float | None
    raw_unit: str | None
    status: DataStatus
    confidence: float                     # 0..1
    source: SourceId | None = None
    method: str | None = None             # "direct" | "idw" | "areal_weighting" | "pixel_overlay" | "mock"
    observed_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    evidence: list[EvidenceSensor] = field(default_factory=list)
    note: str | None = None


@dataclass
class CellIndicators:
    grid_id: str
    time_slot: TimeSlot
    noise: IndicatorValue   # raw_unit "dB"
    light: IndicatorValue   # raw_unit "nW/cm2/sr" (NASA VNP46A3); S-DoT lux only as evidence
    crowd: IndicatorValue   # raw_unit "persons/km2"


@dataclass
class SourceInfo:
    source_id: SourceId
    name: str
    status: str                       # SourceReadiness value
    last_updated_at: datetime | None
    observation_count: int
    missing_rate: float | None
    coverage_bbox: tuple[float, float, float, float] | None = None
    coverage_description: str | None = None
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    spatial_resolution: str | None = None
    update_frequency: str | None = None
    notes: str | None = None


class DataProvider(Protocol):
    """Implemented by services.mock_provider (mock) and the live PostGIS provider."""

    data_mode: str  # "mock" | "live"

    def get_cells(self, grid_ids: list[str], time_slot: TimeSlot) -> dict[str, CellIndicators]:
        """Indicators for the requested cells. Cells without any data may be omitted."""
        ...

    def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
        """Like get_cells for one cell, with `evidence` filled relative to (lng, lat)."""
        ...

    def get_sources(self) -> list[SourceInfo]:
        ...

    def precomputed_slots(self) -> list[TimeSlot]:
        ...

    def check_database(self) -> tuple[str, str | None]:
        """("connected" | "disabled" | "error", message)."""
        ...

    def has_poi(self) -> bool:
        ...

    # Optional (checked with getattr):
    #   covers(grid_id) -> bool : False if the cell is outside the provider's service area
    #                             (e.g. inside SEOUL_BBOX but outside the Seoul boundary) -> 404.
    #   warm() -> None          : load datasets ahead of the first request (called at startup).
