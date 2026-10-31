"""Request dependencies: provider selection and shared query parameters."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Query

from app.config import get_settings
from app.contracts.api_models import Sensitivities
from app.contracts.constants import TimeSlot
from app.contracts.data_interfaces import DataProvider
from app.contracts.errors import ApiError, ErrorCode


@lru_cache
def get_provider() -> DataProvider:
    if get_settings().data_mode == "live":
        # Implemented by the api-engine worker (services/live_provider.py).
        from app.services.live_provider import LiveProvider

        return LiveProvider(get_settings())
    from app.services.mock_provider import MockProvider

    return MockProvider()


Provider = Annotated[DataProvider, Depends(get_provider)]

TimeSlotQ = Annotated[TimeSlot, Query(description="day(07-18) / evening(18-22) / night(22-02) / dawn(02-07), Asia/Seoul")]


def sensitivities(
    sound_sensitivity: Annotated[int, Query(ge=0, le=5, description="소음 민감도 0~5")] = 3,
    light_sensitivity: Annotated[int, Query(ge=0, le=5, description="빛 민감도 0~5")] = 3,
    crowd_sensitivity: Annotated[int, Query(ge=0, le=5, description="혼잡 민감도 0~5")] = 3,
) -> Sensitivities:
    return Sensitivities(sound_sensitivity=sound_sensitivity, light_sensitivity=light_sensitivity,
                         crowd_sensitivity=crowd_sensitivity)


SensQ = Annotated[Sensitivities, Depends(sensitivities)]


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    try:
        nums = tuple(float(p) for p in parts)
    except ValueError:
        nums = ()
    if len(nums) != 4:
        raise ApiError(ErrorCode.invalid_parameter, "bbox must be 4 comma-separated numbers: minLng,minLat,maxLng,maxLat",
                       {"bbox": value})
    return nums  # type: ignore[return-value]
