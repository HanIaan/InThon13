"""Contract inspection consistency tests (Part B).

Verifies that source, raw_unit, method, and status across indicators do not contradict each other.
Light policy follows docs/data-dictionary.md 1장 (2026-10-31): score from NASA VNP46A3 only.
"""
from __future__ import annotations

import pytest


NIGHT_SLOTS = ("evening", "night", "dawn")
CITY_HALL = {"lat": 37.5665, "lng": 126.9780}


def _inspect(client, time_slot: str) -> dict:
    res = client.get("/api/v1/location/inspect", params={**CITY_HALL, "time_slot": time_slot})
    assert res.status_code == 200
    return res.json()


def _assert_light_policy(light: dict) -> None:
    """Light policy (data-dictionary 1장): score from NASA VNP46A3 only, S-DoT lux as evidence only."""
    assert light["source"] == "nasa_black_marble"
    assert light["raw_unit"] == "nW/cm2/sr"
    if light["status"] == "mock":
        assert light["method"] == "mock"
    elif light["status"] == "missing":
        # No usable NASA pixel -> null, never replaced by S-DoT lux.
        assert light["raw_value"] is None
    else:
        # ~463 m pixel over a 200 m cell is never a direct 200 m observation.
        assert light["status"] == "estimated"
        assert light["method"] == "pixel_overlay"

    for sensor in light.get("nearby_sensors") or []:
        assert sensor["source"] == "sdot"
        assert sensor["raw_unit"] == "lux"


@pytest.mark.parametrize("time_slot", NIGHT_SLOTS)
def test_inspect_indicator_unit_and_method_consistency(client, time_slot):
    """Units, sources and methods must match the physical indicator specifications."""
    body = _inspect(client, time_slot)
    inds = body["indicators"]

    # 1. Noise: S-DoT, dB
    noise = inds["noise"]
    assert noise["raw_unit"] == "dB"
    assert noise["source"] == "sdot"
    if noise["status"] == "mock":
        assert noise["method"] == "mock"
    for sensor in noise.get("nearby_sensors") or []:
        assert sensor["raw_unit"] == "dB"
        assert sensor["source"] == "sdot"

    # 2. Light after dark: NASA radiance; S-DoT lux only as nearby evidence
    _assert_light_policy(inds["light"])

    # 3. Crowd: living population density
    crowd = inds["crowd"]
    assert crowd["raw_unit"] == "persons/km2"
    assert crowd["source"] == "living_population"
    if crowd["status"] == "mock":
        assert crowd["method"] == "mock"


def test_light_identical_across_night_slots(client):
    """VNP46A3 is monthly: evening/night/dawn must carry the same light raw_value and period."""
    lights = {slot: _inspect(client, slot)["indicators"]["light"] for slot in NIGHT_SLOTS}
    values = {slot: light["raw_value"] for slot, light in lights.items()}
    assert len(set(values.values())) == 1, f"light raw_value differs across night slots: {values}"
    statuses = {light["status"] for light in lights.values()}
    assert len(statuses) == 1, f"light status differs across night slots: {statuses}"
    periods = {(light.get("period_start"), light.get("period_end")) for light in lights.values()}
    assert len(periods) == 1, f"light period differs across night slots: {periods}"


def test_daytime_not_applicable_light_consistency(client):
    """When indicator is not_applicable, score and raw_value must be null, and note should explain."""
    res = client.get(
        "/api/v1/location/inspect",
        params={"lat": 37.5665, "lng": 126.9780, "time_slot": "day"},
    )
    assert res.status_code == 200
    light = res.json()["indicators"]["light"]
    assert light["status"] == "not_applicable"
    assert light["score"] is None
    assert light["raw_value"] is None
    assert light["note"] is not None and "낮" in light["note"]


def test_mock_indicator_source_name_indicates_mock(client):
    """When data_status is 'mock', source_name should explicitly denote mock data (e.g. '서울 S-DoT 도시데이터 센서 (mock)')."""
    res = client.get(
        "/api/v1/location/inspect",
        params={"lat": 37.5665, "lng": 126.9780, "time_slot": "night"},
    )
    assert res.status_code == 200
    inds = res.json()["indicators"]
    for name, ind in inds.items():
        if ind["status"] == "mock" and ind["source_name"] is not None:
            assert "mock" in ind["source_name"].lower(), (
                f"Indicator {name} has status='mock' but source_name='{ind['source_name']}' lacks mock indication"
            )
