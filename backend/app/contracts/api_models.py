"""Pydantic response models of the v1 API. COORDINATOR-OWNED.

These models ARE the frontend contract. Field names, types and nesting must not change
without frontend agreement. Adding optional fields is allowed only via the coordinator.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .constants import (
    DataStatus,
    Grade,
    Indicator,
    RecommendationMode,
    SourceId,
    SourceReadiness,
    TimeSlot,
)

LngLat = tuple[float, float]  # [longitude, latitude]


# ---------------------------------------------------------------- shared pieces

class Sensitivities(BaseModel):
    sound_sensitivity: int = Field(..., ge=0, le=5)
    light_sensitivity: int = Field(..., ge=0, le=5)
    crowd_sensitivity: int = Field(..., ge=0, le=5)


class Weights(BaseModel):
    """Normalised weights actually applied (sum to 1 over usable indicators, 0 otherwise)."""
    noise: float
    light: float
    crowd: float


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    # One exterior ring, closed (first == last), counter-clockwise, [lng, lat] pairs.
    coordinates: list[list[LngLat]]


class ResponseMeta(BaseModel):
    api_version: str = "v1"
    data_mode: Literal["mock", "live"]
    time_slot: TimeSlot
    sensitivities: Sensitivities
    weights: Weights
    generated_at: datetime


# ---------------------------------------------------------------- /health

class DatabaseHealth(BaseModel):
    status: Literal["connected", "disabled", "error"]
    message: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    api_version: str = "v1"
    data_mode: Literal["mock", "live"]
    database: DatabaseHealth
    data_ready: bool
    server_time: datetime


# ---------------------------------------------------------------- /grid

class GridFeatureProperties(BaseModel):
    grid_id: str
    comfort_score: float | None = Field(None, ge=0, le=100)
    grade: Grade | None = None
    noise_score: float | None = Field(None, ge=0, le=100)
    light_score: float | None = Field(None, ge=0, le=100)
    crowd_score: float | None = Field(None, ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    data_status: DataStatus


class GridFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: PolygonGeometry
    properties: GridFeatureProperties


class GridMeta(ResponseMeta):
    bbox: tuple[float, float, float, float]
    cell_size_m: int
    cell_count: int


class GridFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GridFeature]
    # GeoJSON "foreign member" (RFC 7946 §6.1); map libraries ignore it.
    meta: GridMeta


# ---------------------------------------------------------------- /location/inspect

class NearbySensor(BaseModel):
    sensor_id: str
    source: SourceId
    name: str | None = None
    coordinates: LngLat
    distance_m: float
    raw_value: float | None = None
    raw_unit: str | None = None
    observed_at: datetime | None = None


class IndicatorDetail(BaseModel):
    indicator: Indicator
    score: float | None = Field(None, ge=0, le=100)
    raw_value: float | None = None
    raw_unit: str | None = None  # "dB", "lux", "nW/cm2/sr", "persons/km2"
    status: DataStatus
    source: SourceId | None = None
    source_name: str | None = None
    observed_at: datetime | None = None   # latest observation used
    period_start: datetime | None = None  # aggregation window
    period_end: datetime | None = None
    method: str | None = None  # "direct", "idw", "areal_weighting", "pixel_overlay", "mock"
    nearby_sensors: list[NearbySensor] = Field(default_factory=list)
    note: str | None = None


class InspectIndicators(BaseModel):
    noise: IndicatorDetail
    light: IndicatorDetail
    crowd: IndicatorDetail


class InspectQuery(Sensitivities):
    lat: float
    lng: float
    time_slot: TimeSlot


class LocationInspectResponse(BaseModel):
    query: InspectQuery
    grid_id: str
    grid_center: LngLat
    grid_geometry: PolygonGeometry
    comfort_score: float | None = Field(None, ge=0, le=100)
    grade: Grade | None = None
    confidence: float = Field(..., ge=0, le=1)
    data_status: DataStatus
    weights: Weights
    indicators: InspectIndicators
    notes: list[str]
    meta: ResponseMeta


# ---------------------------------------------------------------- /recommendations

class RecommendationItem(BaseModel):
    rank: int
    type: Literal["grid", "poi"]
    grid_id: str
    name: str | None = None  # POI name; null for grid-area recommendations
    center: LngLat
    geometry: PolygonGeometry
    comfort_score: float = Field(..., ge=0, le=100)
    grade: Grade
    noise_score: float | None = None
    light_score: float | None = None
    crowd_score: float | None = None
    confidence: float = Field(..., ge=0, le=1)
    data_status: DataStatus
    distance_m: float | None = None  # set when searched by center + radius


class RecommendationsMeta(ResponseMeta):
    mode: RecommendationMode
    search: dict  # {"bbox": [...]} or {"center": [lng, lat], "radius_m": n}
    candidate_count: int
    poi_available: bool


class RecommendationsResponse(BaseModel):
    items: list[RecommendationItem]
    meta: RecommendationsMeta


# ---------------------------------------------------------------- /data/status

class SourceCoverage(BaseModel):
    bbox: tuple[float, float, float, float] | None = None
    description: str | None = None
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None


class SourceStatus(BaseModel):
    source_id: SourceId
    name: str
    status: SourceReadiness
    last_updated_at: datetime | None = None
    coverage: SourceCoverage
    observation_count: int
    missing_rate: float | None = Field(None, ge=0, le=1)
    spatial_resolution: str | None = None
    update_frequency: str | None = None
    notes: str | None = None


class GridStatus(BaseModel):
    crs: str
    cell_size_m: int
    cell_count: int
    precomputed_slots: list[TimeSlot]


class DataStatusResponse(BaseModel):
    data_mode: Literal["mock", "live"]
    sources: list[SourceStatus]
    grid: GridStatus
    generated_at: datetime
