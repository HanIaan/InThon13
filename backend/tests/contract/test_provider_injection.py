"""Tests verifying engine behavior with injected custom DataProvider fixtures (Part B)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.api.deps import get_provider
from app.contracts.constants import (
    SEOUL_BBOX,
    DataStatus,
    SourceId,
    SourceReadiness,
    TimeSlot,
)
from app.contracts.data_interfaces import (
    CellIndicators,
    DataProvider,
    IndicatorValue,
    SourceInfo,
)
from app.core import grid
from app.main import app

KST = ZoneInfo("Asia/Seoul")


class BaseFakeProvider(DataProvider):
    data_mode = "live"

    def get_cells(self, grid_ids: list[str], time_slot: TimeSlot) -> dict[str, CellIndicators]:
        return {}

    def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
        return None

    def get_sources(self) -> list[SourceInfo]:
        return [
            SourceInfo(
                source_id=SourceId.sdot,
                name="서울 S-DoT 센서",
                status=SourceReadiness.ready.value,
                last_updated_at=datetime.now(KST),
                observation_count=1000,
                missing_rate=0.01,
            )
        ]

    def precomputed_slots(self) -> list[TimeSlot]:
        return list(TimeSlot)

    def check_database(self) -> tuple[str, str | None]:
        return "connected", None

    def has_poi(self) -> bool:
        return False


def test_empty_provider_handling(client):
    """When the provider has no cell data, grid should be empty and inspect should return missing."""
    app.dependency_overrides[get_provider] = lambda: BaseFakeProvider()

    # 1. Grid
    res = client.get("/api/v1/grid", params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "night"})
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 0
    assert data["meta"]["cell_count"] == 0

    # 2. Inspect coordinate in Seoul (provider returns None)
    res_insp = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.9780, "time_slot": "night"})
    assert res_insp.status_code == 200
    insp = res_insp.json()
    assert insp["comfort_score"] is None
    assert insp["grade"] is None
    assert insp["confidence"] == 0.0
    assert insp["data_status"] == "missing"
    assert insp["indicators"]["noise"]["status"] == "missing"
    assert insp["indicators"]["noise"]["score"] is None
    assert insp["indicators"]["light"]["status"] == "missing"
    assert insp["indicators"]["crowd"]["status"] == "missing"
    assert any("감각지수를 계산하지 않았습니다" in n for n in insp["notes"])

    # 3. Recommendations
    res_rec = client.get("/api/v1/recommendations", params={"mode": "housing", "time_slot": "night", "bbox": "126.97,37.55,127.00,37.58"})
    assert res_rec.status_code == 200
    assert res_rec.json()["items"] == []


def test_mixed_data_status_provider(client):
    """Verify combination rules: all observed -> observed; any mock -> mock; mixed -> estimated."""
    c_lat, c_lng = 37.5665, 126.9780
    ix, iy = grid.cell_of(c_lng, c_lat)
    gid = grid.grid_id(ix, iy)

    def _val(status: DataStatus, raw: float, unit: str):
        return IndicatorValue(raw_value=raw, raw_unit=unit, status=status, confidence=0.8, method="direct")

    # Case 1: All observed
    class AllObservedProvider(BaseFakeProvider):
        def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
            return CellIndicators(
                grid_id=grid_id,
                time_slot=time_slot,
                noise=_val(DataStatus.observed, 50.0, "dB"),
                light=_val(DataStatus.observed, 10.0, "lux"),
                crowd=_val(DataStatus.observed, 15000.0, "persons/km2"),
            )

    app.dependency_overrides[get_provider] = lambda: AllObservedProvider()
    r1 = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "night"})
    assert r1.status_code == 200
    assert r1.json()["data_status"] == "observed"

    # Case 2: Observed + Estimated -> Estimated
    class MixedEstimatedProvider(BaseFakeProvider):
        def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
            return CellIndicators(
                grid_id=grid_id,
                time_slot=time_slot,
                noise=_val(DataStatus.observed, 50.0, "dB"),
                light=_val(DataStatus.estimated, 10.0, "lux"),
                crowd=_val(DataStatus.observed, 15000.0, "persons/km2"),
            )

    app.dependency_overrides[get_provider] = lambda: MixedEstimatedProvider()
    r2 = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "night"})
    assert r2.status_code == 200
    assert r2.json()["data_status"] == "estimated"

    # Case 3: Mock indicator -> overall data_status is mock
    class MockTaintedProvider(BaseFakeProvider):
        def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
            return CellIndicators(
                grid_id=grid_id,
                time_slot=time_slot,
                noise=_val(DataStatus.observed, 50.0, "dB"),
                light=_val(DataStatus.mock, 10.0, "lux"),
                crowd=_val(DataStatus.observed, 15000.0, "persons/km2"),
            )

    app.dependency_overrides[get_provider] = lambda: MockTaintedProvider()
    r3 = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "night"})
    assert r3.status_code == 200
    assert r3.json()["data_status"] == "mock"


def test_nasa_pixel_only_provider(client):
    """A cell with only NASA radiance data (noise and crowd missing).

    Must be used at night, but MUST NOT be used in daytime (light is not_applicable in day).
    """
    c_lat, c_lng = 37.5665, 126.9780
    ix, iy = grid.cell_of(c_lng, c_lat)
    gid = grid.grid_id(ix, iy)

    def _missing():
        return IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0)

    class NasaOnlyProvider(BaseFakeProvider):
        def get_cells(self, grid_ids: list[str], time_slot: TimeSlot) -> dict[str, CellIndicators]:
            if gid in grid_ids:
                return {gid: self.get_cell_detail(gid, time_slot, c_lng, c_lat)}
            return {}

        def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
            if time_slot == TimeSlot.day:
                light = IndicatorValue(
                    raw_value=None,
                    raw_unit=None,
                    status=DataStatus.not_applicable,
                    confidence=0.0,
                    note="낮 시간대에는 빛공해 지표 미적용",
                )
            else:
                light = IndicatorValue(
                    raw_value=25.0,  # 25 nW/cm2/sr
                    raw_unit="nW/cm2/sr",
                    status=DataStatus.observed,
                    confidence=0.85,
                    source=SourceId.nasa_black_marble,
                    method="pixel_overlay",
                )
            return CellIndicators(grid_id=grid_id, time_slot=time_slot, noise=_missing(), light=light, crowd=_missing())

    app.dependency_overrides[get_provider] = lambda: NasaOnlyProvider()

    # 1. At Night: NASA light is valid and used
    r_night = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "night"})
    assert r_night.status_code == 200
    night_data = r_night.json()
    assert night_data["comfort_score"] is not None
    assert night_data["grade"] is not None
    assert night_data["data_status"] == "observed"
    assert night_data["weights"]["light"] == 1.0
    assert night_data["weights"]["noise"] == 0.0
    assert night_data["weights"]["crowd"] == 0.0
    assert night_data["indicators"]["light"]["raw_unit"] == "nW/cm2/sr"
    assert night_data["indicators"]["light"]["score"] is not None

    # 2. At Day: Light must NOT be used by day!
    # With noise & crowd missing, and light not applicable, there are no usable indicators.
    r_day = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "day"})
    assert r_day.status_code == 200
    day_data = r_day.json()
    assert day_data["comfort_score"] is None
    assert day_data["grade"] is None
    assert day_data["data_status"] == "missing"
    assert day_data["weights"]["light"] == 0.0
    assert day_data["weights"]["noise"] == 0.0
    assert day_data["weights"]["crowd"] == 0.0
    assert day_data["indicators"]["light"]["status"] == "not_applicable"
    assert day_data["indicators"]["light"]["score"] is None


def test_single_available_indicator_renormalization(client):
    """When only 1 indicator has data, other missing indicators must be null and not dilute the comfort_score."""
    c_lat, c_lng = 37.5665, 126.9780
    ix, iy = grid.cell_of(c_lng, c_lat)
    gid = grid.grid_id(ix, iy)

    class OnlyNoiseProvider(BaseFakeProvider):
        def get_cell_detail(self, grid_id: str, time_slot: TimeSlot, lng: float, lat: float) -> CellIndicators | None:
            return CellIndicators(
                grid_id=grid_id,
                time_slot=time_slot,
                noise=IndicatorValue(raw_value=40.0, raw_unit="dB", status=DataStatus.observed, confidence=0.9),  # 40dB is perfect 100.0 score
                light=IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0),
                crowd=IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0),
            )

    app.dependency_overrides[get_provider] = lambda: OnlyNoiseProvider()
    res = client.get("/api/v1/location/inspect", params={"lat": c_lat, "lng": c_lng, "time_slot": "night"})
    assert res.status_code == 200
    data = res.json()
    assert data["indicators"]["noise"]["score"] == 100.0
    assert data["indicators"]["light"]["score"] is None
    assert data["indicators"]["crowd"]["score"] is None

    # comfort_score should be 100.0, not diluted by missing indicators
    assert data["comfort_score"] == 100.0
    assert data["grade"] == "A"
    assert data["weights"]["noise"] == 1.0
    assert data["weights"]["light"] == 0.0
    assert data["weights"]["crowd"] == 0.0
    assert data["data_status"] == "observed"


def test_database_error_health_response(client):
    """When the provider database check returns an error, health should report degraded."""
    class BrokenDbProvider(BaseFakeProvider):
        def check_database(self) -> tuple[str, str | None]:
            return "error", "connection to PostgreSQL timed out after 5000ms"

    app.dependency_overrides[get_provider] = lambda: BrokenDbProvider()
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "degraded"
    assert data["database"]["status"] == "error"
    assert "timed out" in data["database"]["message"]
