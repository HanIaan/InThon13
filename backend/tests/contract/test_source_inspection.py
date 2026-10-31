"""Contract inspection consistency tests (Part B).

Verifies that source, raw_unit, method, and status across indicators do not contradict each other.
Identifies discrepancies in existing implementation and marks them with xfail.
"""
from __future__ import annotations

import pytest


def test_inspect_indicator_unit_and_method_consistency(client):
    """Units and methods must match the physical indicator specifications."""
    res = client.get(
        "/api/v1/location/inspect",
        params={"lat": 37.5665, "lng": 126.9780, "time_slot": "night"},
    )
    assert res.status_code == 200
    inds = res.json()["indicators"]

    # 1. Noise: unit dB, method mock
    noise = inds["noise"]
    assert noise["raw_unit"] == "dB"
    assert noise["status"] == "mock"
    assert noise["method"] == "mock"
    assert noise["source"] == "sdot"

    # 2. Light at night: unit lux (S-DoT) or nW/cm2/sr (NASA)
    light = inds["light"]
    assert light["raw_unit"] in ("lux", "nW/cm2/sr")
    assert light["status"] == "mock"
    assert light["method"] == "mock"
    assert light["source"] == "sdot"

    # 3. Crowd: unit persons/km2
    crowd = inds["crowd"]
    assert crowd["raw_unit"] == "persons/km2"
    assert crowd["status"] == "mock"
    assert crowd["method"] == "mock"
    assert crowd["source"] == "living_population"

    # Check evidence sensors consistency
    for sensor in noise.get("nearby_sensors", []):
        assert sensor["raw_unit"] == "dB"
        assert sensor["source"] == "sdot"

    for sensor in light.get("nearby_sensors", []):
        assert sensor["raw_unit"] == light["raw_unit"]
        assert sensor["source"] == "sdot"


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
