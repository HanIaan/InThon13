"""Honesty, scoring, and business logic contract tests (Part B)."""
from __future__ import annotations

import pytest


def test_mock_data_status_honesty(client):
    """When running in mock mode, data_status must strictly be 'mock'."""
    # 1. Grid features
    r_grid = client.get("/api/v1/grid", params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "night"})
    assert r_grid.status_code == 200
    features = r_grid.json().get("features", [])
    for f in features:
        props = f.get("properties", {})
        if props.get("comfort_score") is not None:
            assert props.get("data_status") == "mock"

    # 2. Location inspect
    r_insp = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.9780, "time_slot": "night"})
    assert r_insp.status_code == 200
    insp = r_insp.json()
    assert insp.get("data_status") == "mock"
    for ind in insp.get("indicators", {}).values():
        if ind.get("score") is not None:
            assert ind.get("status") == "mock"

    # 3. Recommendations
    r_rec = client.get("/api/v1/recommendations", params={"mode": "housing", "time_slot": "night", "bbox": "126.97,37.55,127.00,37.58"})
    assert r_rec.status_code == 200
    for item in r_rec.json().get("items", []):
        assert item.get("data_status") == "mock"


def test_daytime_light_indicator_exclusion(client):
    """During 'day' (07:00-17:59), light indicator must be not_applicable, score null, and weight 0."""
    # Grid
    r_grid = client.get("/api/v1/grid", params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "day"})
    assert r_grid.status_code == 200
    data = r_grid.json()
    assert data["meta"]["weights"]["light"] == 0.0

    for feat in data.get("features", []):
        props = feat.get("properties", {})
        assert props.get("light_score") is None

    # Inspect
    r_insp = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.9780, "time_slot": "day"})
    assert r_insp.status_code == 200
    insp = r_insp.json()
    assert insp["weights"]["light"] == 0.0
    light_ind = insp["indicators"]["light"]
    assert light_ind["score"] is None
    assert light_ind["status"] == "not_applicable"


def test_all_zero_sensitivities_uses_equal_weights(client):
    """When all sensitivities are 0, weights must be equal across usable indicators."""
    # At night (3 usable indicators: noise, light, crowd)
    r_night = client.get(
        "/api/v1/grid",
        params={
            "bbox": "126.97,37.55,127.00,37.58",
            "time_slot": "night",
            "sound_sensitivity": 0,
            "light_sensitivity": 0,
            "crowd_sensitivity": 0,
        },
    )
    assert r_night.status_code == 200
    w_night = r_night.json()["meta"]["weights"]
    assert w_night["noise"] == pytest.approx(0.333, abs=0.005)
    assert w_night["light"] == pytest.approx(0.333, abs=0.005)
    assert w_night["crowd"] == pytest.approx(0.333, abs=0.005)

    # By day (2 usable indicators: noise, crowd; light is not applicable)
    r_day = client.get(
        "/api/v1/grid",
        params={
            "bbox": "126.97,37.55,127.00,37.58",
            "time_slot": "day",
            "sound_sensitivity": 0,
            "light_sensitivity": 0,
            "crowd_sensitivity": 0,
        },
    )
    assert r_day.status_code == 200
    w_day = r_day.json()["meta"]["weights"]
    assert w_day["noise"] == pytest.approx(0.5, abs=0.005)
    assert w_day["crowd"] == pytest.approx(0.5, abs=0.005)
    assert w_day["light"] == 0.0


def test_weights_sum_to_one(client):
    """Weights actually applied must sum to 1.0 (over usable indicators) within rounding tolerance."""
    for slot in ("day", "evening", "night", "dawn"):
        res = client.get(
            "/api/v1/grid",
            params={
                "bbox": "126.97,37.55,127.00,37.58",
                "time_slot": slot,
                "sound_sensitivity": 4,
                "light_sensitivity": 2,
                "crowd_sensitivity": 5,
            },
        )
        assert res.status_code == 200
        w = res.json()["meta"]["weights"]
        total = w["noise"] + w["light"] + w["crowd"]
        assert total == pytest.approx(1.0, abs=0.005)


def test_grade_thresholds_consistency(client):
    """Grade must follow: A (>=70.0), B (40.0..69.9), C (<40.0), or null if comfort_score is null."""
    res = client.get(
        "/api/v1/grid",
        params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "night"},
    )
    assert res.status_code == 200
    features = res.json().get("features", [])
    assert len(features) > 0

    for feat in features:
        props = feat.get("properties", {})
        score = props.get("comfort_score")
        grade = props.get("grade")

        if score is None:
            assert grade is None
        elif score >= 70.0:
            assert grade == "A", f"Score {score} should have grade A, got {grade}"
        elif score >= 40.0:
            assert grade == "B", f"Score {score} should have grade B, got {grade}"
        else:
            assert grade == "C", f"Score {score} should have grade C, got {grade}"


def test_recommendations_sorted_descending(client):
    """Recommendations items must be strictly sorted by comfort_score desc, then confidence desc."""
    res = client.get(
        "/api/v1/recommendations",
        params={
            "mode": "housing",
            "time_slot": "night",
            "bbox": "126.95,37.53,127.05,37.60",
            "limit": 20,
        },
    )
    assert res.status_code == 200
    items = res.json().get("items", [])
    assert len(items) > 1

    for i in range(len(items) - 1):
        curr = items[i]
        nxt = items[i + 1]
        c_score = curr["comfort_score"]
        n_score = nxt["comfort_score"]

        assert c_score >= n_score, f"Items not sorted by comfort_score desc: rank {curr['rank']} ({c_score}) vs rank {nxt['rank']} ({n_score})"

        if c_score == n_score:
            assert curr["confidence"] >= nxt["confidence"], f"Ties must be broken by confidence desc: rank {curr['rank']} ({curr['confidence']}) vs rank {nxt['rank']} ({nxt['confidence']})"
