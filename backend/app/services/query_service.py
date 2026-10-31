"""Builds API responses from a DataProvider. Provider-agnostic: mock and live share this."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.contracts import api_models as m
from app.contracts.constants import (
    GRID_CELL_SIZE_M,
    GRID_CRS,
    LIGHT_APPLICABLE_SLOTS,
    MAX_GRID_CELLS_PER_REQUEST,
    DataStatus,
    Indicator,
    RecommendationMode,
    TimeSlot,
)
from app.contracts.data_interfaces import CellIndicators, DataProvider, IndicatorValue
from app.contracts.errors import ApiError, ErrorCode
from app.core import grid
from app.core.scoring import ScoredCell, weights_for

KST = ZoneInfo("Asia/Seoul")

SOURCE_NAMES = {
    "sdot": "서울 S-DoT 도시데이터 센서",
    "living_population": "서울 생활인구",
    "nasa_black_marble": "NASA Black Marble",
}


def _now() -> datetime:
    return datetime.now(KST)


def _sens_map(s: m.Sensitivities) -> dict[Indicator, int]:
    return {Indicator.noise: s.sound_sensitivity, Indicator.light: s.light_sensitivity, Indicator.crowd: s.crowd_sensitivity}


def _weights(w: dict[Indicator, float]) -> m.Weights:
    return m.Weights(noise=round(w[Indicator.noise], 3), light=round(w[Indicator.light], 3), crowd=round(w[Indicator.crowd], 3))


def _meta(provider: DataProvider, slot: TimeSlot, sens: m.Sensitivities, weights: m.Weights) -> dict:
    return dict(data_mode=provider.data_mode, time_slot=slot, sensitivities=sens, weights=weights, generated_at=_now())


def _request_weights(sens: m.Sensitivities, slot: TimeSlot) -> m.Weights:
    """Weights as they apply to a fully-populated cell for this slot (for meta)."""
    scores = {Indicator.noise: 1.0, Indicator.light: 1.0 if slot in LIGHT_APPLICABLE_SLOTS else None, Indicator.crowd: 1.0}
    return _weights(weights_for(scores, _sens_map(sens)))


def _validate_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    min_lng, min_lat, max_lng, max_lat = bbox
    if not (-180 <= min_lng <= 180 and -180 <= max_lng <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ApiError(ErrorCode.invalid_parameter, "bbox values out of WGS84 range", {"bbox": list(bbox)})
    if min_lng >= max_lng or min_lat >= max_lat:
        raise ApiError(ErrorCode.invalid_parameter, "bbox must be minLng,minLat,maxLng,maxLat with min < max",
                       {"bbox": list(bbox)})
    clipped = grid.clip_to_seoul(bbox)
    if clipped is None:
        raise ApiError(ErrorCode.out_of_coverage, "bbox does not intersect the Seoul service area",
                       {"bbox": list(bbox)})
    count = grid.bbox_cell_count(clipped)
    if count > MAX_GRID_CELLS_PER_REQUEST:
        raise ApiError(ErrorCode.bbox_too_large,
                       f"bbox covers {count} grid cells; the limit is {MAX_GRID_CELLS_PER_REQUEST}. Zoom in.",
                       {"cell_count": count, "max_cells": MAX_GRID_CELLS_PER_REQUEST})
    return clipped


def _score_cells(provider: DataProvider, cells: list[grid.Cell], slot: TimeSlot, sens: m.Sensitivities) -> list[tuple[grid.Cell, ScoredCell]]:
    ids = [grid.grid_id(*c) for c in cells]
    data = provider.get_cells(ids, slot)
    smap = _sens_map(sens)
    return [(c, ScoredCell(data[gid], smap)) for c, gid in zip(cells, ids) if gid in data]


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


# ---------------------------------------------------------------- endpoints

def health(provider: DataProvider) -> m.HealthResponse:
    db_status, db_msg = provider.check_database()
    sources = provider.get_sources()
    data_ready = any(s.status in ("ready", "partial", "mock") for s in sources)
    ok = data_ready and db_status != "error"
    return m.HealthResponse(status="ok" if ok else "degraded", data_mode=provider.data_mode,
                            database=m.DatabaseHealth(status=db_status, message=db_msg),
                            data_ready=data_ready, server_time=_now())


def grid_features(provider: DataProvider, bbox, slot: TimeSlot, sens: m.Sensitivities) -> m.GridFeatureCollection:
    clipped = _validate_bbox(bbox)
    scored = _score_cells(provider, grid.cells_in_bbox(clipped), slot, sens)
    features = []
    for (ix, iy), sc in scored:
        gid = sc.cell.grid_id
        features.append(m.GridFeature(
            id=gid,
            geometry=m.PolygonGeometry(coordinates=grid.cell_polygon(ix, iy)),
            properties=m.GridFeatureProperties(
                grid_id=gid, comfort_score=sc.comfort_score, grade=sc.grade,
                noise_score=sc.scores[Indicator.noise], light_score=sc.scores[Indicator.light],
                crowd_score=sc.scores[Indicator.crowd], confidence=sc.confidence, data_status=sc.data_status,
            ),
        ))
    meta = m.GridMeta(**_meta(provider, slot, sens, _request_weights(sens, slot)),
                      bbox=clipped, cell_size_m=GRID_CELL_SIZE_M, cell_count=len(features))
    return m.GridFeatureCollection(features=features, meta=meta)


# Display precision per raw unit (response only; precomputed values are untouched).
RAW_DECIMALS = {"dB": 1, "lux": 1, "nW/cm2/sr": 1, "persons/km2": 0}
CROWD_PERIOD_GAP = timedelta(days=30)
# 서울시 행정동 생활인구 시계열 종료일 (docs/data-dictionary.md 1장·3장).
LIVING_POPULATION_SERIES_END = "2026-07-31"


def _round_raw(value: float | None, unit: str | None) -> float | None:
    digits = RAW_DECIMALS.get(unit)
    if value is None or digits is None:
        return value
    return float(round(value, digits)) if digits else float(round(value))


def _source_name(v: IndicatorValue) -> str | None:
    name = SOURCE_NAMES.get(v.source.value) if v.source else None
    if name and v.status is DataStatus.mock:
        name += " (mock)"
    return name


INDICATOR_LABELS = {Indicator.noise: "소음", Indicator.light: "빛", Indicator.crowd: "혼잡"}


def _no_score_note(sc: ScoredCell, sens: m.Sensitivities, slot: TimeSlot) -> str:
    """Why comfort_score is null: selected senses (sensitivity > 0) all lack usable data."""
    smap = _sens_map(sens)
    selected = [k for k in INDICATOR_LABELS if smap[k] > 0]
    if not selected:
        return "사용 가능한 지표가 없어 감각지수를 계산하지 않았습니다."
    names = ", ".join(INDICATOR_LABELS[k] for k in selected)
    note = f"선택한 감각({names})의 유효한 데이터가 없어 감각지수를 계산하지 않았습니다."
    # Only suggest other senses when one of them actually has data here.
    if any(sc.scores[k] is not None for k in INDICATOR_LABELS if k not in selected):
        note += " 다른 감각의 민감도를 높이면 점수를 볼 수 있습니다."
    if Indicator.light in selected and slot not in LIGHT_APPLICABLE_SLOTS:
        note += " 낮 시간대에는 빛공해 지표를 적용하지 않습니다."
    return note


def _date_range(v: IndicatorValue) -> str:
    # period_end is exclusive; show the last covered day.
    last = (v.period_end - timedelta(microseconds=1)).date()
    return f"{v.period_start.date().isoformat()}~{last.isoformat()}"


def _crowd_note(cell: CellIndicators) -> str:
    """One live notice: crowd is a past living-population series, plus the noise-period
    difference when crowd ends >= 30 days before noise measurement starts."""
    crowd, noise = cell.crowd, cell.noise
    if crowd.period_start is None or crowd.period_end is None:
        return (f"혼잡도는 과거 생활인구 자료 기준입니다. "
                f"서울시 행정동 생활인구 시계열은 {LIVING_POPULATION_SERIES_END}에 종료됐습니다.")
    first = crowd.period_start.date()
    last = (crowd.period_end - timedelta(microseconds=1)).date()
    months = f"{first.year}년 {first.month}월"
    if (last.year, last.month) != (first.year, first.month):
        months += f"~{last.year}년 {last.month}월"
    head = f"혼잡도는 {months}({_date_range(crowd)}) 생활인구 기준의 과거 자료"
    if (noise.period_start is not None and noise.period_end is not None
            and noise.period_start - crowd.period_end >= CROWD_PERIOD_GAP):
        return f"{head}로, 소음 측정 기간({_date_range(noise)})과 다릅니다."
    return f"{head}입니다."


def _indicator_detail(ind: Indicator, v: IndicatorValue, score: float | None) -> m.IndicatorDetail:
    return m.IndicatorDetail(
        indicator=ind, score=score, raw_value=_round_raw(v.raw_value, v.raw_unit), raw_unit=v.raw_unit, status=v.status,
        source=v.source, source_name=_source_name(v),
        observed_at=v.observed_at, period_start=v.period_start, period_end=v.period_end, method=v.method,
        nearby_sensors=[m.NearbySensor(sensor_id=e.sensor_id, source=e.source, name=e.name, coordinates=(e.lng, e.lat),
                                       distance_m=e.distance_m, raw_value=_round_raw(e.raw_value, e.raw_unit), raw_unit=e.raw_unit,
                                       observed_at=e.observed_at) for e in v.evidence],
        note=v.note,
    )


def _missing(ind: Indicator) -> IndicatorValue:
    return IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0,
                          note="이 위치에 사용할 수 있는 데이터가 없습니다.")


def inspect(provider: DataProvider, lat: float, lng: float, slot: TimeSlot, sens: m.Sensitivities) -> m.LocationInspectResponse:
    if not grid.in_seoul_extent(lng, lat):
        raise ApiError(ErrorCode.out_of_coverage, "coordinate is outside the Seoul service area",
                       {"lat": lat, "lng": lng})
    ix, iy = grid.cell_of(lng, lat)
    gid = grid.grid_id(ix, iy)
    cell = provider.get_cell_detail(gid, slot, lng, lat) or CellIndicators(
        grid_id=gid, time_slot=slot, noise=_missing(Indicator.noise),
        light=IndicatorValue(None, None, DataStatus.not_applicable, 0.0) if slot == TimeSlot.day else _missing(Indicator.light),
        crowd=_missing(Indicator.crowd))
    sc = ScoredCell(cell, _sens_map(sens))
    notes = ["건물 단위 실측값이 아니라 좌표 주변 200m 격자의 환경 추정값입니다."]
    if sc.data_status is DataStatus.mock:
        notes.append("Mock 모드: 모든 값은 데모용 합성 데이터입니다.")
    if sc.comfort_score is None:
        notes.append(_no_score_note(sc, sens, slot))
    if provider.data_mode == "live":
        notes.append(_crowd_note(cell))
    return m.LocationInspectResponse(
        query=m.InspectQuery(lat=lat, lng=lng, time_slot=slot, **sens.model_dump()),
        grid_id=gid, grid_center=grid.cell_center(ix, iy),
        grid_geometry=m.PolygonGeometry(coordinates=grid.cell_polygon(ix, iy)),
        comfort_score=sc.comfort_score, grade=sc.grade, confidence=sc.confidence, data_status=sc.data_status,
        weights=_weights(sc.weights),
        indicators=m.InspectIndicators(
            noise=_indicator_detail(Indicator.noise, cell.noise, sc.scores[Indicator.noise]),
            light=_indicator_detail(Indicator.light, cell.light, sc.scores[Indicator.light]),
            crowd=_indicator_detail(Indicator.crowd, cell.crowd, sc.scores[Indicator.crowd]),
        ),
        notes=notes,
        meta=m.ResponseMeta(**_meta(provider, slot, sens, _weights(sc.weights))),
    )


def recommendations(provider: DataProvider, mode: RecommendationMode, slot: TimeSlot, sens: m.Sensitivities, limit: int,
                    bbox=None, center: tuple[float, float] | None = None, radius_m: float | None = None) -> m.RecommendationsResponse:
    if bbox is not None:
        area = _validate_bbox(bbox)
        cells = grid.cells_in_bbox(area)
        search = {"bbox": list(area)}
    else:
        lng, lat = center
        if not grid.in_seoul_extent(lng, lat):
            raise ApiError(ErrorCode.out_of_coverage, "center is outside the Seoul service area", {"lat": lat, "lng": lng})
        dlat = radius_m / 111_000
        dlng = radius_m / (111_000 * 0.79)
        area = _validate_bbox((lng - dlng, lat - dlat, lng + dlng, lat + dlat))
        cells = [c for c in grid.cells_in_bbox(area) if grid.distance_m(lng, lat, *grid.cell_center(*c)) <= radius_m]
        search = {"center": [lng, lat], "radius_m": radius_m}

    scored = [(c, sc) for c, sc in _score_cells(provider, cells, slot, sens) if sc.comfort_score is not None]
    # Ties broken by confidence, then grid_id for determinism.
    scored.sort(key=lambda t: (-t[1].comfort_score, -t[1].confidence, t[1].cell.grid_id))
    items = []
    for rank, ((ix, iy), sc) in enumerate(scored[:limit], start=1):
        cx, cy = grid.cell_center(ix, iy)
        items.append(m.RecommendationItem(
            rank=rank, type="grid", grid_id=sc.cell.grid_id, name=None, center=(cx, cy),
            geometry=m.PolygonGeometry(coordinates=grid.cell_polygon(ix, iy)),
            comfort_score=sc.comfort_score, grade=sc.grade,
            noise_score=sc.scores[Indicator.noise], light_score=sc.scores[Indicator.light], crowd_score=sc.scores[Indicator.crowd],
            confidence=sc.confidence, data_status=sc.data_status,
            distance_m=round(grid.distance_m(center[0], center[1], cx, cy), 1) if center else None,
        ))
    meta = m.RecommendationsMeta(**_meta(provider, slot, sens, _request_weights(sens, slot)), mode=mode, search=search,
                                 candidate_count=len(scored), poi_available=provider.has_poi())
    return m.RecommendationsResponse(items=items, meta=meta)


def data_status(provider: DataProvider) -> m.DataStatusResponse:
    sources = [m.SourceStatus(
        source_id=s.source_id, name=s.name, status=s.status, last_updated_at=s.last_updated_at,
        coverage=m.SourceCoverage(bbox=s.coverage_bbox, description=s.coverage_description,
                                  temporal_start=s.temporal_start, temporal_end=s.temporal_end),
        observation_count=s.observation_count, missing_rate=s.missing_rate, spatial_resolution=s.spatial_resolution,
        update_frequency=s.update_frequency, notes=s.notes,
    ) for s in provider.get_sources()]
    x0, y0, x1, y1 = grid.bbox_cell_range(grid.clip_to_seoul((-180, -90, 180, 90)))
    return m.DataStatusResponse(
        data_mode=provider.data_mode, sources=sources,
        grid=m.GridStatus(crs=GRID_CRS, cell_size_m=GRID_CELL_SIZE_M, cell_count=getattr(provider, "cell_count", (x1 - x0 + 1) * (y1 - y0 + 1)),
                          precomputed_slots=provider.precomputed_slots()),
        generated_at=_now(),
    )
