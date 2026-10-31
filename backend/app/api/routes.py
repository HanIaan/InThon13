"""v1 HTTP routes. Thin layer: parse/validate parameters, delegate to services.query_service."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.contracts import api_models as m
from app.contracts.constants import (
    RECOMMENDATION_DEFAULT_LIMIT,
    RECOMMENDATION_DEFAULT_RADIUS_M,
    RECOMMENDATION_MAX_LIMIT,
    RECOMMENDATION_MAX_RADIUS_M,
    RecommendationMode,
)
from app.contracts.errors import ApiError, ErrorCode, ErrorResponse
from app.services import query_service as qs

from .deps import Provider, SensQ, TimeSlotQ, parse_bbox

ERRORS = {code: {"model": ErrorResponse} for code in (400, 404, 422, 500, 503)}

router = APIRouter(responses=ERRORS)

BboxQ = Annotated[str, Query(description="minLng,minLat,maxLng,maxLat (WGS84)", examples=["126.97,37.55,127.00,37.58"])]


@router.get("/health", response_model=m.HealthResponse, tags=["system"], summary="서버·DB·데이터 준비 상태")
def health(provider: Provider):
    return qs.health(provider)


@router.get("/grid", response_model=m.GridFeatureCollection, tags=["map"], summary="bbox 내 200m 격자 감각지수 (GeoJSON)")
def grid(provider: Provider, bbox: BboxQ, time_slot: TimeSlotQ, sens: SensQ):
    return qs.grid_features(provider, parse_bbox(bbox), time_slot, sens)


@router.get("/location/inspect", response_model=m.LocationInspectResponse, tags=["map"], summary="좌표 클릭 상세정보")
def location_inspect(
    provider: Provider,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    time_slot: TimeSlotQ,
    sens: SensQ,
):
    return qs.inspect(provider, lat, lng, time_slot, sens)


@router.get("/recommendations", response_model=m.RecommendationsResponse, tags=["recommendations"],
            summary="주거·휴식 적합 후보 (bbox 또는 중심좌표+반경)")
def recommendations(
    provider: Provider,
    mode: Annotated[RecommendationMode, Query()],
    time_slot: TimeSlotQ,
    sens: SensQ,
    bbox: Annotated[str | None, Query(description="minLng,minLat,maxLng,maxLat. lat/lng와 동시 사용 불가")] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[float, Query(gt=0, le=RECOMMENDATION_MAX_RADIUS_M)] = RECOMMENDATION_DEFAULT_RADIUS_M,
    limit: Annotated[int, Query(ge=1, le=RECOMMENDATION_MAX_LIMIT)] = RECOMMENDATION_DEFAULT_LIMIT,
):
    has_center = lat is not None and lng is not None
    if (bbox is None) == (not has_center) or (lat is None) != (lng is None):
        raise ApiError(ErrorCode.invalid_parameter, "provide either bbox, or both lat and lng (not both forms)",
                       {"bbox": bbox, "lat": lat, "lng": lng})
    if bbox is not None:
        return qs.recommendations(provider, mode, time_slot, sens, limit, bbox=parse_bbox(bbox))
    return qs.recommendations(provider, mode, time_slot, sens, limit, center=(lng, lat), radius_m=radius_m)


@router.get("/data/status", response_model=m.DataStatusResponse, tags=["system"], summary="데이터 소스별 준비 상태")
def data_status(provider: Provider):
    return qs.data_status(provider)
