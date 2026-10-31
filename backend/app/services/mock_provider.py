"""Deterministic synthetic data for frontend development. Everything is data_status="mock".

Values are smooth functions of position (a few urban "hotspots") plus a per-cell hash
jitter, so the map looks plausible and is stable across requests. ~3 % of cells are
missing on purpose so the frontend exercises the null path. NOT real measurements.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.contracts.constants import (
    LIGHT_APPLICABLE_SLOTS,
    SEOUL_BBOX,
    TIME_SLOT_HOURS,
    DataStatus,
    SourceId,
    SourceReadiness,
    TimeSlot,
)
from app.contracts.data_interfaces import (
    CellIndicators,
    EvidenceSensor,
    IndicatorValue,
    SourceInfo,
)
from app.core import grid

KST = ZoneInfo("Asia/Seoul")
MOCK_REFERENCE_DATE = datetime(2026, 10, 1, tzinfo=KST)

# (lng, lat, intensity, radius_deg) — synthetic busy centres.
_HOTSPOTS = [
    (127.0276, 37.4979, 1.0, 0.020),
    (126.9780, 37.5665, 0.9, 0.022),
    (126.9246, 37.5563, 0.8, 0.015),
    (127.1002, 37.5133, 0.7, 0.016),
    (126.8956, 37.5172, 0.6, 0.018),
    (127.0557, 37.6540, 0.5, 0.020),
]

_SLOT_NOISE_OFFSET = {TimeSlot.day: 6.0, TimeSlot.evening: 3.0, TimeSlot.night: -5.0, TimeSlot.dawn: -9.0}
_SLOT_CROWD_FACTOR = {TimeSlot.day: 1.3, TimeSlot.evening: 1.1, TimeSlot.night: 0.75, TimeSlot.dawn: 0.6}
_SLOT_LUX_FACTOR = {TimeSlot.evening: 1.2, TimeSlot.night: 1.0, TimeSlot.dawn: 0.7}


def _hash01(*parts: object) -> float:
    h = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64


def _urban(lng: float, lat: float) -> float:
    v = 0.15
    for hx, hy, k, r in _HOTSPOTS:
        v += k * math.exp(-((lng - hx) ** 2 + (lat - hy) ** 2) / (2 * r * r))
    return min(v, 1.2)


def _period(slot: TimeSlot) -> tuple[datetime, datetime, datetime]:
    hours = TIME_SLOT_HOURS[slot]
    start = MOCK_REFERENCE_DATE.replace(hour=hours[0])
    end = start + timedelta(hours=len(hours))
    return start, end, end - timedelta(hours=1)


class MockProvider:
    data_mode = "mock"

    def _cell(self, gid: str, slot: TimeSlot, with_evidence_at: tuple[float, float] | None = None) -> CellIndicators | None:
        ix, iy = grid.parse_grid_id(gid)
        lng, lat = grid.cell_center(ix, iy)
        if not grid.in_seoul_extent(lng, lat) or _hash01(gid, "missing") < 0.03:
            return None
        u = _urban(lng, lat)
        start, end, last = _period(slot)
        common = dict(status=DataStatus.mock, method="mock", observed_at=last, period_start=start, period_end=end,
                      note="데모용 합성 데이터입니다. 실제 측정값이 아닙니다.")

        noise_db = 42 + 28 * u + _SLOT_NOISE_OFFSET[slot] + 6 * (_hash01(gid, "n") - 0.5)
        noise = IndicatorValue(raw_value=round(noise_db, 1), raw_unit="dB", confidence=round(0.5 + 0.3 * _hash01(gid, "nc"), 2),
                               source=SourceId.sdot, **common)

        if slot in LIGHT_APPLICABLE_SLOTS:
            lux = (2 + 70 * u ** 1.5) * _SLOT_LUX_FACTOR[slot] * (0.7 + 0.6 * _hash01(gid, "l"))
            light = IndicatorValue(raw_value=round(lux, 1), raw_unit="lux", confidence=round(0.45 + 0.3 * _hash01(gid, "lc"), 2),
                                   source=SourceId.sdot, **common)
        else:
            light = IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.not_applicable, confidence=0.0,
                                   note="낮 시간대에는 빛공해 지표를 적용하지 않습니다.")

        density = (3_000 + 45_000 * u) * _SLOT_CROWD_FACTOR[slot] * (0.75 + 0.5 * _hash01(gid, "c"))
        crowd = IndicatorValue(raw_value=round(density), raw_unit="persons/km2", confidence=round(0.55 + 0.25 * _hash01(gid, "cc"), 2),
                               source=SourceId.living_population, **common)

        if with_evidence_at:
            plng, plat = with_evidence_at
            for ind, unit in ((noise, "dB"), (light, "lux")):
                if ind.raw_value is None:
                    continue
                for k in range(3):
                    slng = plng + 0.004 * (_hash01(gid, unit, k, "x") - 0.5)
                    slat = plat + 0.003 * (_hash01(gid, unit, k, "y") - 0.5)
                    ind.evidence.append(EvidenceSensor(
                        sensor_id=f"MOCK-{gid[-9:]}-{k}", source=SourceId.sdot, name=f"Mock sensor {k + 1}",
                        lng=round(slng, 6), lat=round(slat, 6),
                        distance_m=round(grid.distance_m(plng, plat, slng, slat), 1),
                        raw_value=round(ind.raw_value * (0.9 + 0.2 * _hash01(gid, unit, k)), 1), raw_unit=unit,
                        observed_at=last,
                    ))
                ind.evidence.sort(key=lambda e: e.distance_m)
        return CellIndicators(grid_id=gid, time_slot=slot, noise=noise, light=light, crowd=crowd)

    def get_cells(self, grid_ids: list[str], time_slot: TimeSlot) -> dict[str, CellIndicators]:
        out = {}
        for gid in grid_ids:
            c = self._cell(gid, time_slot)
            if c is not None:
                out[gid] = c
        return out

    def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
        return self._cell(grid_id, time_slot, with_evidence_at=(lng, lat))

    def get_sources(self) -> list[SourceInfo]:
        common = dict(status=SourceReadiness.mock.value, last_updated_at=MOCK_REFERENCE_DATE, missing_rate=0.03,
                      coverage_bbox=SEOUL_BBOX, coverage_description="서울 전역 (합성 데모 데이터)",
                      temporal_start=MOCK_REFERENCE_DATE, temporal_end=MOCK_REFERENCE_DATE + timedelta(days=1))
        return [
            SourceInfo(source_id=SourceId.sdot, name="서울 S-DoT 도시데이터 센서 (mock)", observation_count=0,
                       spatial_resolution="point sensors", update_frequency="hourly",
                       notes="Mock 모드: 실제 S-DoT 데이터가 아닙니다.", **common),
            SourceInfo(source_id=SourceId.living_population, name="서울 생활인구 (mock)", observation_count=0,
                       spatial_resolution="행정동", update_frequency="daily",
                       notes="Mock 모드: 실제 생활인구 데이터가 아닙니다.", **common),
            SourceInfo(source_id=SourceId.nasa_black_marble, name="NASA Black Marble VNP46 (mock)", observation_count=0,
                       spatial_resolution="15 arc-second (~460 m)", update_frequency="daily / monthly",
                       notes="Mock 모드: 빛공해는 S-DoT 조도만 합성 사용합니다.", **common),
        ]

    def precomputed_slots(self) -> list[TimeSlot]:
        return list(TimeSlot)

    def check_database(self) -> tuple[str, str | None]:
        return "disabled", "mock mode: database not used"

    def has_poi(self) -> bool:
        return False
