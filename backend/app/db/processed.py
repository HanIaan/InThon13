"""Strict, offline readers for the coordinator-owned processed-file contract.

Missing optional input files mean an unavailable source. A boundary file is required:
we never replace Seoul's administrative boundary with its rectangular extent.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, fields
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from shapely.geometry import shape

from app.contracts.constants import DataStatus, SourceId
from app.contracts.data_interfaces import (
    LivingPopulationRecord, NightLightRecord, SensorLocation, SensorObservation,
)

KST = ZoneInfo("Asia/Seoul")


@dataclass
class ProcessedData:
    sensors: list[SensorLocation]
    observations: list[SensorObservation]
    population: list[LivingPopulationRecord]
    lights: list[NightLightRecord]
    boundaries: list[dict]


def _csv(directory: Path, filename: str, record_type) -> list:
    path = directory / filename
    if not path.exists():
        return []
    adapter = TypeAdapter(record_type)
    names = {f.name for f in fields(record_type)}
    result = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or set(reader.fieldnames) - names:
            raise ValueError(f"{filename}: invalid CSV header")
        for line, row in enumerate(reader, 2):
            try:
                # Empty fields are null; dataclass defaults apply to omitted columns.
                record = adapter.validate_python({k: (v if v and v.strip() else None) for k, v in row.items()})
                for f in fields(record):
                    value = getattr(record, f.name)
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("non-finite number")
                result.append(record)
            except (ValueError, TypeError):
                raise ValueError(f"{filename}: invalid record at line {line}") from None
    return result


def _unique(records, key, filename):
    keys = [key(r) for r in records]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{filename}: duplicate record key")


def load_processed(directory: str | Path) -> ProcessedData:
    directory = Path(directory)
    sensors = _csv(directory, "sdot_sensors.csv", SensorLocation)
    observations = _csv(directory, "sdot_observations.csv", SensorObservation)
    population = _csv(directory, "living_population.csv", LivingPopulationRecord)
    lights = _csv(directory, "nasa_night_lights.csv", NightLightRecord)
    _unique(sensors, lambda r: r.sensor_id, "sdot_sensors.csv")
    _unique(observations, lambda r: (r.sensor_id, r.observed_at), "sdot_observations.csv")
    _unique(population, lambda r: (r.dong_code, r.stat_date, r.hour), "living_population.csv")
    _unique(lights, lambda r: (r.pixel_id, r.product, r.period_start, r.period_end), "nasa_night_lights.csv")
    for s in sensors:
        if not (-180 <= s.lng <= 180 and -90 <= s.lat <= 90) or s.source != SourceId.sdot:
            raise ValueError("sdot_sensors.csv: invalid location or source")
        if s.location_status not in (DataStatus.observed, DataStatus.estimated):
            raise ValueError("sdot_sensors.csv: live locations must be observed or estimated")
        if s.installed_at and s.installed_at.tzinfo is None:
            raise ValueError("sdot_sensors.csv: installed_at must be timezone-aware")
    for r in observations:
        if r.observed_at.tzinfo is None or r.sample_count < 1:
            raise ValueError("sdot_observations.csv: timezone-aware time and positive sample_count required")
    for r in population:
        if not 0 <= r.hour <= 23 or r.total_population < 0 or (r.area_km2 is not None and r.area_km2 <= 0):
            raise ValueError("living_population.csv: invalid hour, population or area")
    for r in lights:
        if not (-180 <= r.lng <= 180 and -90 <= r.lat <= 90) or not 0 < r.pixel_size_deg <= 1:
            raise ValueError("nasa_night_lights.csv: invalid pixel geometry")
        if r.period_end < r.period_start:
            raise ValueError("nasa_night_lights.csv: reversed period")
    path = directory / "admin_dong_boundaries.geojson"
    if not path.exists():
        raise ValueError("admin_dong_boundaries.geojson is required for precomputation")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if document.get("type") != "FeatureCollection" or not document.get("features"):
        raise ValueError("admin_dong_boundaries.geojson: nonempty FeatureCollection required")
    boundaries = document["features"]
    codes = set()
    for feature in boundaries:
        code = feature.get("properties", {}).get("dong_code")
        geom = shape(feature["geometry"])
        if not code or code in codes or geom.geom_type not in ("Polygon", "MultiPolygon") or geom.is_empty or not geom.is_valid:
            raise ValueError("admin_dong_boundaries.geojson: invalid/duplicate administrative geometry")
        if not (-180 <= geom.bounds[0] <= geom.bounds[2] <= 180 and -90 <= geom.bounds[1] <= geom.bounds[3] <= 90):
            raise ValueError("admin_dong_boundaries.geojson: geometry must be WGS84")
        codes.add(code)
    return ProcessedData(sensors, observations, population, lights, boundaries)
