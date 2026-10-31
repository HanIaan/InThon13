"""Offline live-data preparation. Run from backend/:

    ../.venv/bin/python -m app.services.precompute [--local-only] [--max-distance-m 1000]

Reads only the five processed contract files. Publishes cell_indicators.sqlite;
if DATABASE_URL is configured, also replaces the engine's PostGIS dataset atomically.
A failed DB publication leaves the successfully built local snapshot available.

Rules (heuristics, not calibrated uncertainty probabilities):
* Each valid hourly row has equal temporal weight, regardless of sample_count.
  Per-sensor slot means are averaged equally within the cell; otherwise use up to
  8 sensors within max_distance_m of its centre, inverse squared metric distance.
  A geocoded/estimated sensor location cannot yield observed status.
* Confidence = directness * (0.5 + 0.5*min(n_sensors/3,1)) *
  (0.5 + 0.5*min(n_samples/24,1)). Directness is 1 for direct measured locations,
  0.75 for geocoded locations, or 0.7*(1-0.5*nearest_distance/max_distance) for IDW.
* Crowd = sum(dong density * intersected area) / area with valid population data.
  Unknown dong portions are not zero-filled. Confidence = 0.65 * available area /
  cell's administrative land area * min(1, hourly rows / expected slot hours).
  Supplied dong area is used when available, otherwise EPSG:5179 polygon area.
* Light prefers usable S-DoT lux (direct or IDW). Only if unavailable, night/dawn
  use NASA pixel-overlap-weighted radiance. Never mix lux with radiance or use
  NASA for day/evening. Day is not_applicable. Pixel dates are preserved, with
  no claim they resolve a particular hour. Only Mandatory_Quality_Flag 0/1 is
  accepted; unknown/poor/fill quality is excluded. cloud_flag is retained in DB,
  not decoded here (ingestion owns product-specific cloud filtering).
  Latest period_end per pixel is selected; ties prefer shortest period then
  product name. Pixel confidence = 0.55 * covered fraction (0.8 penalty for QF=1).
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from pyproj import Transformer
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from app.config import get_settings
from app.contracts.constants import (
    DataStatus, SourceId, TimeSlot, TIME_SLOT_HOURS, GRID_ORIGIN_X,
    GRID_ORIGIN_Y, GRID_CELL_SIZE_M, hour_to_time_slot,
)
from app.contracts.data_interfaces import CellIndicators, EvidenceSensor, IndicatorValue, SourceInfo
from app.core import grid
from app.db.processed import load_processed
from app.db.snapshot import Snapshot, SNAPSHOT_NAME, write_snapshot

KST = ZoneInfo("Asia/Seoul")
PROJECT = Transformer.from_crs(4326, 5179, always_xy=True).transform


def missing(day_light=False):
    return IndicatorValue(None, None, DataStatus.not_applicable if day_light else DataStatus.missing, 0.0)


def metric_cell(ix, iy):
    x, y = GRID_ORIGIN_X + ix * GRID_CELL_SIZE_M, GRID_ORIGIN_Y + iy * GRID_CELL_SIZE_M
    return box(x, y, x + GRID_CELL_SIZE_M, y + GRID_CELL_SIZE_M)


class SensorField:
    def __init__(self, records):
        self.records = records
        self.points = [Point(PROJECT(e.lng, e.lat)) for e, _, _, _ in records]
        self.tree = STRtree(self.points)
        self.by_cell = defaultdict(list)
        for i, (e, _, _, _) in enumerate(records):
            self.by_cell[grid.cell_of(e.lng, e.lat)].append(i)

    def value(self, ix, iy, max_distance):
        cx, cy = metric_cell(ix, iy).centroid.coords[0]
        point = Point(cx, cy)
        indices = self.by_cell.get((ix, iy), [])
        direct = bool(indices)
        if not direct:
            indices = sorted((int(i) for i in self.tree.query(point.buffer(max_distance))
                              if point.distance(self.points[i]) <= max_distance),
                             key=lambda i: (point.distance(self.points[i]), self.records[i][0].sensor_id))[:8]
        if not indices:
            return missing()
        distances = [point.distance(self.points[i]) for i in indices]
        weights = [1.0 if direct else 1 / max(d, 1.0)**2 for d in distances]
        selected = [self.records[i] for i in indices]
        observed = direct and all(r[3] == DataStatus.observed for r in selected)
        directness = (1 if observed else 0.75) if direct else 0.7 * (1 - 0.5 * min(distances) / max_distance)
        samples = sum(r[1] for r in selected)
        confidence = directness * (0.5 + 0.5 * min(len(indices) / 3, 1)) * (0.5 + 0.5 * min(samples / 24, 1))
        return IndicatorValue(
            raw_value=sum(r[0].raw_value * w for r, w in zip(selected, weights)) / sum(weights),
            raw_unit=selected[0][0].raw_unit, status=DataStatus.observed if observed else DataStatus.estimated,
            confidence=round(confidence, 4), source=SourceId.sdot, method="direct" if direct else "idw",
            observed_at=max(r[0].observed_at for r in selected), period_start=min(r[2] for r in selected),
            period_end=max(r[0].observed_at for r in selected) + timedelta(hours=1),
            evidence=[replace(r[0], distance_m=round(d, 1)) for r, d in zip(selected, distances)],
            note="시간대별 유효 시간 관측의 센서 평균; 추정 위치 센서는 estimated로 표시합니다.",
        )


def sensor_fields(data):
    locations = {s.sensor_id: s for s in data.sensors}
    buckets = defaultdict(list)
    for row in data.observations:
        if row.quality_flag or row.sensor_id not in locations:
            continue
        stamp = row.observed_at.astimezone(KST)
        slot = hour_to_time_slot(stamp.hour)
        for ind, value in (("noise", row.noise_db), ("light", row.illuminance_lux)):
            if value is not None and value >= 0:
                buckets[slot, ind, row.sensor_id].append((value, stamp, row.sample_count))
    records = defaultdict(list)
    for (slot, ind, sid), rows in buckets.items():
        s = locations[sid]
        evidence = EvidenceSensor(sid, SourceId.sdot, s.lng, s.lat, 0.0, mean(r[0] for r in rows),
                                  "dB" if ind == "noise" else "lux", max(r[1] for r in rows), s.name)
        records[slot, ind].append((evidence, sum(r[2] for r in rows), min(r[1] for r in rows), s.location_status))
    return {(slot, ind): SensorField(records[slot, ind]) for slot in TimeSlot for ind in ("noise", "light")}


def population_fields(data, dong_geoms):
    buckets = defaultdict(list)
    for row in data.population:
        if row.dong_code not in dong_geoms:
            continue
        area = row.area_km2 or dong_geoms[row.dong_code].area / 1e6
        stamp = datetime.combine(row.stat_date, time(row.hour), KST)
        buckets[hour_to_time_slot(row.hour), row.dong_code].append((row.total_population / area, stamp))
    return buckets


def crowd_value(polygon, land_area, overlaps, population, slot):
    parts = [(area, population[slot, code]) for code, area in overlaps if population.get((slot, code))]
    if not parts:
        return missing()
    area = sum(a for a, _ in parts)
    stamps = [stamp for _, rows in parts for _, stamp in rows]
    temporal = sum(a * min(1, len(rows) / len(TIME_SLOT_HOURS[slot])) for a, rows in parts) / area
    return IndicatorValue(
        sum(a * mean(v for v, _ in rows) for a, rows in parts) / area, "persons/km2",
        DataStatus.estimated, round(0.65 * min(1, area / land_area) * temporal, 4),
        source=SourceId.living_population, method="areal_weighting", observed_at=max(stamps),
        period_start=min(stamps), period_end=max(stamps) + timedelta(hours=1),
        note=f"행정동 유효 밀도의 교차면적 가중평균; 셀 내 행정구역 면적 대비 유효 면적 {min(1, area / land_area):.1%}.",
    )


def usable_pixel(r):
    return r.radiance is not None and r.radiance >= 0 and r.quality_flag in (0, 1)


class PixelField:
    def __init__(self, lights):
        selected = {}
        for r in sorted((r for r in lights if usable_pixel(r)),
                        key=lambda r: (r.period_end, -(r.period_end-r.period_start).days, r.product)):
            selected[r.pixel_id] = r
        self.records = list(selected.values())
        self.polygons = [transform(PROJECT, box(r.lng-r.pixel_size_deg/2, r.lat-r.pixel_size_deg/2,
                                                r.lng+r.pixel_size_deg/2, r.lat+r.pixel_size_deg/2)) for r in self.records]
        self.tree = STRtree(self.polygons)

    def value(self, polygon):
        parts = [(int(i), polygon.intersection(self.polygons[i])) for i in self.tree.query(polygon)]
        parts = [(i, p) for i, p in parts if p.area > 0]
        if not parts:
            return missing()
        total = sum(p.area for _, p in parts)
        covered = unary_union([p for _, p in parts]).area / polygon.area
        quality = sum(p.area * (1 if self.records[i].quality_flag == 0 else 0.8) for i, p in parts) / total
        start = min(self.records[i].period_start for i, _ in parts)
        end = max(self.records[i].period_end for i, _ in parts)
        return IndicatorValue(
            sum(p.area * self.records[i].radiance for i, p in parts) / total, "nW/cm2/sr",
            DataStatus.estimated, round(0.55 * min(1, covered) * quality, 4),
            source=SourceId.nasa_black_marble, method="pixel_overlay",
            period_start=datetime.combine(start, time.min, KST),
            period_end=datetime.combine(end + timedelta(days=1), time.min, KST),
            note="S-DoT 조도 부재 시 NASA 야간 픽셀 대체값; 시간대별 직접 관측이 아닙니다. 관측 시각은 미제공.",
        )


def _bounds(geometries):
    return tuple(unary_union(geometries).bounds) if geometries else None


def source_metadata(data, fields, dong_geoms, now):
    """Counts are input rows, missing_rate is unusable input indicator values / total.

    Coverage and temporal bounds describe usable rows, not Seoul-wide coverage.
    Last update is this snapshot's load time. Provider cadence is left unknown;
    a collection timestamp is not evidence of an upstream publication schedule.
    """
    output = []
    valid_ids = {s.sensor_id for s in data.sensors}
    valid_obs = [r for r in data.observations if not r.quality_flag and r.sensor_id in valid_ids]
    usable_values = sum(v is not None and v >= 0 for r in valid_obs for v in (r.noise_db, r.illuminance_lux))
    valid_obs = [r for r in valid_obs if any(v is not None and v >= 0 for v in (r.noise_db, r.illuminance_lux))]
    ids = {r.sensor_id for r in valid_obs}
    obs_times = [r.observed_at.astimezone(KST) for r in valid_obs]
    valid_pop = [r for r in data.population if r.dong_code in dong_geoms]
    pop_times = [datetime.combine(r.stat_date, time(r.hour), KST) for r in valid_pop]
    pop_codes = {r.dong_code for r in valid_pop}
    valid_lights = [r for r in data.lights if usable_pixel(r)]
    configs = [
        (SourceId.sdot, "서울 S-DoT 도시데이터 센서", len(data.observations), usable_values, 2 * len(data.observations),
         [Point(s.lng, s.lat) for s in data.sensors if s.sensor_id in ids], obs_times, "point sensors", "유효 소음/조도 값 비율; 품질 플래그·위치 없는 관측 제외"),
        (SourceId.living_population, "서울 생활인구", len(data.population), len(valid_pop), len(data.population),
         [shape(f["geometry"]) for f in data.boundaries if f["properties"]["dong_code"] in pop_codes], pop_times,
         "행정동", "경계와 결합 가능한 생활인구 행 비율"),
        (SourceId.nasa_black_marble, "NASA Black Marble", len(data.lights), len(valid_lights), len(data.lights),
         [box(r.lng-r.pixel_size_deg/2, r.lat-r.pixel_size_deg/2, r.lng+r.pixel_size_deg/2, r.lat+r.pixel_size_deg/2) for r in valid_lights],
         [datetime.combine(d, time.min, KST) for r in valid_lights for d in (r.period_start, r.period_end)],
         ", ".join(f"{s:g} degrees" for s in sorted({r.pixel_size_deg for r in valid_lights})) or None,
         "유효 방사휘도 및 Mandatory_Quality_Flag 0/1 비율; " + ", ".join(sorted({r.product for r in valid_lights}))),
    ]
    for sid, name, count, valid, denominator, geoms, times, resolution, note in configs:
        rate = 1 - valid / denominator if denominator else None
        output.append(SourceInfo(
            sid, name, "not_loaded" if not count else ("ready" if valid == denominator else "partial") if valid else "error",
            now if count else None, count, rate, _bounds(geoms),
            "적재된 유효 레코드의 공간 범위 (서울 전역 확보를 의미하지 않음)",
            min(times) if times else None, max(times) if times else None, resolution, None,
            note + "; last_updated_at은 사전계산 적재 시각, 원천 갱신주기는 미확인.",
        ))
    return output


def build_snapshot(directory, max_distance_m=1000.0):
    if not math.isfinite(max_distance_m) or max_distance_m <= 0:
        raise ValueError("max_distance_m must be positive and finite")
    data = load_processed(directory)
    dong_geoms = {f["properties"]["dong_code"]: transform(PROJECT, shape(f["geometry"])) for f in data.boundaries}
    boundary = unary_union(list(dong_geoms.values()))
    extent = grid.clip_to_seoul(_bounds([shape(f["geometry"]) for f in data.boundaries]))
    if extent is None:
        raise ValueError("administrative boundaries do not intersect Seoul service extent")
    codes = list(dong_geoms)
    dong_tree = STRtree(list(dong_geoms.values()))
    fields = sensor_fields(data)
    population = population_fields(data, dong_geoms)
    pixels = PixelField(data.lights)
    cells, grids = [], []
    for ix, iy in grid.cells_in_bbox(extent):
        polygon = metric_cell(ix, iy)
        land_area = polygon.intersection(boundary).area
        if land_area <= 1e-6:
            continue
        gid = grid.grid_id(ix, iy)
        grids.append(dict(grid_id=gid, ix=ix, iy=iy,
                          geometry=dict(type="Polygon", coordinates=grid.cell_polygon(ix, iy))))
        overlaps = [(codes[i], polygon.intersection(dong_geoms[codes[i]]).area) for i in dong_tree.query(polygon)]
        overlaps = [(code, area) for code, area in overlaps if area > 1e-6]
        nasa = pixels.value(polygon)
        for slot in TimeSlot:
            noise = fields[slot, "noise"].value(ix, iy, max_distance_m)
            light = missing(True) if slot == TimeSlot.day else fields[slot, "light"].value(ix, iy, max_distance_m)
            if slot in (TimeSlot.night, TimeSlot.dawn) and light.raw_value is None:
                light = nasa
            cells.append(CellIndicators(gid, slot, noise, light, crowd_value(polygon, land_area, overlaps, population, slot)))
    if not grids:
        raise ValueError("no intersecting grid cells")
    sources = source_metadata(data, fields, dong_geoms, datetime.now(KST))
    sensors = {(slot.value, ind): [r[0] for r in field.records] for (slot, ind), field in fields.items()}
    return Snapshot(cells, grids, sources, sensors), data


def precompute(settings, *, local_only=False, max_distance_m=1000.0):
    snapshot, data = build_snapshot(settings.processed_data_dir, max_distance_m)
    path = Path(settings.processed_data_dir) / SNAPSHOT_NAME
    write_snapshot(path, snapshot)
    if settings.database_url and not local_only:
        from app.db.postgres import publish
        publish(settings.database_url, snapshot, data)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="publish SQLite only, even if DATABASE_URL is configured")
    parser.add_argument("--max-distance-m", type=float, default=1000.0)
    args = parser.parse_args()
    try:
        snapshot = precompute(get_settings(), local_only=args.local_only, max_distance_m=args.max_distance_m)
    except Exception:
        # Connection errors can contain credentials; never print their original text.
        parser.exit(1, "Precomputation failed. Check processed file contracts, directory permissions and database readiness.\n")
    print(f"Published {len(snapshot.grids)} cells / {len(snapshot.cells)} cell-slot rows to {SNAPSHOT_NAME}")


if __name__ == "__main__":
    main()
