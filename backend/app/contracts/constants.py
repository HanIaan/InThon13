"""Shared constants of the v1 API contract.

COORDINATOR-OWNED. Workers must not edit this file; request changes instead.
"""
from __future__ import annotations

from enum import Enum

API_VERSION = "v1"
API_PREFIX = "/api/v1"
TIMEZONE = "Asia/Seoul"

# ---- Spatial ----
WGS84 = "EPSG:4326"
# Korea 2000 / Unified CS. Metric CRS used to build the 200 m grid.
GRID_CRS = "EPSG:5179"
GRID_CELL_SIZE_M = 200
# Grid origin (lower-left) in GRID_CRS, a multiple of the cell size, south-west of Seoul.
GRID_ORIGIN_X = 934_000
GRID_ORIGIN_Y = 1_934_000
GRID_ID_PREFIX = "SEL200"

# Seoul service extent (minLng, minLat, maxLng, maxLat).
SEOUL_BBOX = (126.76, 37.41, 127.19, 37.72)

# Requests whose bbox would cover more cells than this are rejected (400 bbox_too_large).
MAX_GRID_CELLS_PER_REQUEST = 6000

RECOMMENDATION_DEFAULT_LIMIT = 10
RECOMMENDATION_MAX_LIMIT = 50
RECOMMENDATION_MAX_RADIUS_M = 5000
RECOMMENDATION_DEFAULT_RADIUS_M = 1500

# ---- Scoring ----
SENSITIVITY_MIN = 0
SENSITIVITY_MAX = 5
GRADE_A_MIN = 70.0  # score >= 70 -> A
GRADE_B_MIN = 40.0  # 40 <= score < 70 -> B, else C


class TimeSlot(str, Enum):
    day = "day"          # 07:00-17:59
    evening = "evening"  # 18:00-21:59
    night = "night"      # 22:00-01:59
    dawn = "dawn"        # 02:00-06:59


# Hours (Asia/Seoul, 0-23) belonging to each time slot.
TIME_SLOT_HOURS: dict[TimeSlot, tuple[int, ...]] = {
    TimeSlot.day: tuple(range(7, 18)),
    TimeSlot.evening: (18, 19, 20, 21),
    TimeSlot.night: (22, 23, 0, 1),
    TimeSlot.dawn: (2, 3, 4, 5, 6),
}

# Light pollution is only meaningful after dark. During `day` the light indicator is
# `not_applicable` and excluded from the weighted score.
LIGHT_APPLICABLE_SLOTS = frozenset({TimeSlot.evening, TimeSlot.night, TimeSlot.dawn})


class DataStatus(str, Enum):
    observed = "observed"              # direct measurement within the cell / at the point
    estimated = "estimated"            # spatial interpolation or areal disaggregation
    mock = "mock"                      # synthetic demo data
    missing = "missing"                # no usable data
    not_applicable = "not_applicable"  # indicator meaningless for this slot (e.g. light by day)


class Indicator(str, Enum):
    noise = "noise"
    light = "light"
    crowd = "crowd"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class RecommendationMode(str, Enum):
    housing = "housing"
    rest = "rest"


class SourceId(str, Enum):
    sdot = "sdot"
    living_population = "living_population"
    nasa_black_marble = "nasa_black_marble"


class SourceReadiness(str, Enum):
    ready = "ready"
    partial = "partial"
    not_loaded = "not_loaded"
    mock = "mock"
    error = "error"


def hour_to_time_slot(hour: int) -> TimeSlot:
    """Map an Asia/Seoul local hour (0-23) to its time slot."""
    for slot, hours in TIME_SLOT_HOURS.items():
        if hour in hours:
            return slot
    raise ValueError(f"hour out of range: {hour}")
