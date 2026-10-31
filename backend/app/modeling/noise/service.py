"""Serving adapter: predict 200 m cell noise from the POI model artifact.

Enabled with ``NOISE_MODEL=poi`` (default off) in ``services.precompute``. Only this
adapter imports the modeling package at runtime; when the flag is off nothing here
is imported and the existing IDW/direct rules run unchanged.

Rules, matching the contract's honesty requirements:

* cells with a direct sensor keep the existing direct rule;
* every other cell gets the model prediction — status ``estimated``, method
  ``model``, source ``sdot`` — including cells outside the 1 km IDW radius
  (baseline falls back to a 5 km IDW with a lower confidence factor);
* confidence is the documented CV-MAE heuristic stored in the artifact, not a
  calibrated probability;
* evidence lists the sensors behind the IDW baseline, never a fabricated value.

Required inputs (paths via environment, because the store file and OSM cache are
large and not committed):
``NOISE_POI_STORES_CSV``    상가업소 CSV (2026-09-30 snapshot)
``NOISE_OSM_DIR``           directory with ``seoul_roads.geojson`` and ``industrial_areas.geojson``
``NOISE_MODEL_ARTIFACT``    optional artifact override (default: packaged v1)
"""

from __future__ import annotations

import math
import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point

from app.contracts.constants import DataStatus, SourceId, TimeSlot
from app.contracts.data_interfaces import IndicatorValue
from app.core import grid
from app.modeling.noise import idw, industry, model as noise_model, osm, poi
from app.modeling.noise import features as spatial

FLAG_ENV = "NOISE_MODEL"
STORES_ENV = "NOISE_POI_STORES_CSV"
OSM_DIR_ENV = "NOISE_OSM_DIR"
ARTIFACT_ENV = "NOISE_MODEL_ARTIFACT"
DEFAULT_ARTIFACT = Path(__file__).resolve().parent / "artifacts" / "noise_poi_model_v1.json"
MAX_EVIDENCE = 8
_TO_METRIC = Transformer.from_crs(4326, 5179, always_xy=True)


def parse_flag(value: str | None) -> bool:
    """True only for the explicit experimental value ``poi``."""
    return (value or "").strip().lower() == "poi"


class NoisePoiPredictor:
    """Per-cell POI-model predictions for every time slot."""

    def __init__(self, model: noise_model.NoiseModel, stores: pd.DataFrame,
                 lines: list, polygons: list) -> None:
        self.model = model
        self.stores = stores
        self.lines = lines
        self.polygons = polygons
        self.features: pd.DataFrame | None = None
        self.keys: dict[tuple[int, int], str] = {}
        self.slot_records: dict[str, list] = {}
        self.primary: dict[str, idw.NoiseIDW] = {}
        self.fallback: dict[str, idw.NoiseIDW] = {}

    @classmethod
    def from_paths(cls, artifact_path: str | Path, stores_csv: str | Path,
                   osm_dir: str | Path) -> "NoisePoiPredictor":
        osm_dir = Path(osm_dir)
        model = noise_model.NoiseModel.load(artifact_path)
        stores = poi.load_stores(stores_csv)
        lines = spatial.project_lines(osm.load_features(osm_dir / "seoul_roads.geojson"))
        polygons = industry.project_polygons(osm.load_industrial(osm_dir / "industrial_areas.geojson"))
        return cls(model, stores, lines, polygons)

    # ---- setup -----------------------------------------------------------
    def attach_sensors(self, slot_records: dict[str, list]) -> None:
        """Build the primary/fallback IDW fields from per-slot sensor records.

        ``slot_records[slot]`` holds ``(EvidenceSensor, samples, first_time,
        location_status)`` tuples, the same shape ``precompute.sensor_fields``
        produces for the noise indicator.
        """
        baseline = self.model.baseline
        primary = baseline["primary"]
        fallback = baseline["fallback"]
        self.slot_records = slot_records
        for slot, records in slot_records.items():
            usable = [
                (float(evidence.lng), float(evidence.lat), float(evidence.raw_value), str(evidence.sensor_id))
                for evidence, _, _, _ in records
                if evidence.raw_value is not None and math.isfinite(float(evidence.raw_value))
            ]
            self.primary[slot] = idw.NoiseIDW(
                usable,
                max_distance_m=float(primary["max_distance_m"]),
                k=int(primary["k"]),
            )
            self.fallback[slot] = idw.NoiseIDW(
                usable,
                max_distance_m=float(fallback["max_distance_m"]),
                k=int(fallback["k"]),
            )

    def prepare(self, cells: list[tuple[int, int]]) -> None:
        """Compute road/POI/industrial features once for every cell centre."""
        if not self.slot_records:
            raise RuntimeError("attach_sensors() must run before prepare()")
        self.keys = {(ix, iy): f"{ix}-{iy}" for ix, iy in cells}
        points = pd.DataFrame(
            [
                {"sensor_id": self.keys[(ix, iy)], "lng": lng, "lat": lat}
                for ix, iy in cells
                for lng, lat in [grid.cell_center(ix, iy)]
            ]
        )
        road = spatial.compute_road_rail_features(points, self.lines)
        shops = poi.compute_poi_features(points, self.stores)
        industrial = industry.compute_industrial_features(points, self.polygons)
        self.features = (
            road.merge(shops, on="sensor_id", how="left")
            .merge(industrial, on="sensor_id", how="left")
            .set_index("sensor_id")
        )

    # ---- prediction ------------------------------------------------------
    def _baseline(self, slot: str, lng: float, lat: float) -> tuple[float | None, bool]:
        value = self.primary[slot].predict(lng, lat)
        if value is not None and math.isfinite(value):
            return value, False
        value = self.fallback[slot].predict(lng, lat)
        return (value if value is not None and math.isfinite(value) else None), True

    def _nearby(self, slot: str, lng: float, lat: float, max_distance_m: float):
        point = Point(_TO_METRIC.transform(lng, lat))
        candidates = []
        for record in self.slot_records[slot]:
            evidence = record[0]
            distance = point.distance(Point(_TO_METRIC.transform(evidence.lng, evidence.lat)))
            if distance <= max_distance_m:
                candidates.append((distance, str(evidence.sensor_id), record))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[:MAX_EVIDENCE]

    def value(self, ix: int, iy: int, slot: TimeSlot | str) -> IndicatorValue:
        slot_value = slot.value if isinstance(slot, TimeSlot) else str(slot)
        key = self.keys[(ix, iy)]
        lng, lat = grid.cell_center(ix, iy)
        row = self.features.loc[key]
        a_pred, fallback_used = self._baseline(slot_value, lng, lat)
        values = {name: float(row[name]) for name in row.index}
        values["a_pred"] = a_pred if a_pred is not None else math.nan
        predicted = self.model.predict(slot_value, values)
        confidence = self.model.confidence(slot_value, fallback_used=fallback_used)

        max_distance = float(
            self.model.baseline["fallback" if fallback_used else "primary"]["max_distance_m"]
        )
        nearby = self._nearby(slot_value, lng, lat, max_distance)
        evidence = []
        observed_at = None
        period_start = None
        for distance, _, record in nearby:
            sensor_evidence, _, first_time, _ = record
            evidence.append(
                type(sensor_evidence)(
                    sensor_id=sensor_evidence.sensor_id,
                    source=sensor_evidence.source,
                    lng=sensor_evidence.lng,
                    lat=sensor_evidence.lat,
                    distance_m=round(distance, 1),
                    raw_value=sensor_evidence.raw_value,
                    raw_unit=sensor_evidence.raw_unit,
                    observed_at=sensor_evidence.observed_at,
                    name=sensor_evidence.name,
                )
            )
            if sensor_evidence.observed_at is not None:
                observed_at = max(filter(None, [observed_at, sensor_evidence.observed_at]))
            if first_time is not None:
                period_start = min(filter(None, [period_start, first_time]))
        period_end = observed_at + timedelta(hours=1) if observed_at is not None else None

        mae = self.model.slot_mae(slot_value)
        note = (
            f"상가·공장 근접도 기반 회귀 예측(estimated) — {self.model.model_id} "
            f"v{self.model.model_version}; 직접 센서가 없어 IDW 기준값 + 학습된 잔차 보정. "
        )
        if mae is not None:
            note += f"이 시간대 CV MAE {mae:.2f} dB, 신뢰도는 CV 오차 기반 휴리스틱입니다. "
        if fallback_used:
            note += "반경 1 km 내 센서가 없어 5 km IDW 기준값을 사용했습니다(외삽 위험)."
        elif not nearby:
            note += "주변 센서가 없어 학습 중앙값을 사용했습니다."

        return IndicatorValue(
            raw_value=float(predicted),
            raw_unit="dB",
            status=DataStatus.estimated,
            confidence=confidence,
            source=SourceId.sdot,
            method=self.model.method,
            observed_at=observed_at,
            period_start=period_start,
            period_end=period_end,
            evidence=evidence,
            note=note,
        )


def predictor_from_env(
    processed_data_dir: str | Path, flag: str | None = None
) -> NoisePoiPredictor | None:
    """Build the predictor when the flag is ``poi``; otherwise return None.

    ``flag`` defaults to ``$NOISE_MODEL``. Every other input is read from the
    environment because the store CSV and OSM cache are not committed.
    """
    if not parse_flag(flag if flag is not None else os.environ.get(FLAG_ENV)):
        return None
    stores_csv = os.environ.get(STORES_ENV)
    if not stores_csv:
        raise ValueError(f"{FLAG_ENV}=poi requires {STORES_ENV} (상가업소 CSV path)")
    artifact = os.environ.get(ARTIFACT_ENV) or DEFAULT_ARTIFACT
    osm_dir = os.environ.get(OSM_DIR_ENV) or Path(processed_data_dir).resolve().parent / "raw" / "osm"
    return NoisePoiPredictor.from_paths(artifact, stores_csv, osm_dir)
