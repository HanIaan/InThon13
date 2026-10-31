"""Error format envelopes and CORS contract tests (Part B)."""
from __future__ import annotations

import pytest

from app.api.deps import get_provider
from app.contracts.data_interfaces import DataProvider
from app.contracts.errors import ApiError, ErrorCode
from app.main import app


def _assert_error_envelope(data: dict, expected_code: str):
    assert "error" in data, f"Response missing 'error' envelope: {data}"
    err = data["error"]
    assert "code" in err, f"Error missing 'code': {err}"
    assert "message" in err, f"Error missing 'message': {err}"
    assert err["code"] == expected_code


def test_400_invalid_parameter_bbox_syntax(client):
    res = client.get("/api/v1/grid", params={"bbox": "invalid,bbox", "time_slot": "night"})
    assert res.status_code == 400
    _assert_error_envelope(res.json(), "invalid_parameter")


def test_400_invalid_parameter_bbox_inverted(client):
    # minLng (127.1) > maxLng (126.9)
    res = client.get("/api/v1/grid", params={"bbox": "127.1,37.5,126.9,37.6", "time_slot": "night"})
    assert res.status_code == 400
    _assert_error_envelope(res.json(), "invalid_parameter")


def test_400_bbox_too_large(client):
    # Bbox spanning far beyond 6000 cells limit
    res = client.get("/api/v1/grid", params={"bbox": "126.8,37.42,127.15,37.70", "time_slot": "night"})
    assert res.status_code == 400
    _assert_error_envelope(res.json(), "bbox_too_large")


def test_400_recommendations_conflicting_search_parameters(client):
    # Both bbox and lat/lng
    res = client.get(
        "/api/v1/recommendations",
        params={
            "mode": "housing",
            "time_slot": "night",
            "bbox": "126.97,37.55,127.00,37.58",
            "lat": 37.56,
            "lng": 126.98,
        },
    )
    assert res.status_code == 400
    _assert_error_envelope(res.json(), "invalid_parameter")


def test_400_recommendations_missing_both_search_forms(client):
    # Neither bbox nor lat/lng
    res = client.get("/api/v1/recommendations", params={"mode": "housing", "time_slot": "night"})
    assert res.status_code == 400
    _assert_error_envelope(res.json(), "invalid_parameter")


def test_404_out_of_coverage_coordinate(client):
    # Coordinate outside Seoul service area (e.g. Busan / Jeju)
    res = client.get("/api/v1/location/inspect", params={"lat": 35.1796, "lng": 129.0756, "time_slot": "night"})
    assert res.status_code == 404
    _assert_error_envelope(res.json(), "out_of_coverage")


def test_404_out_of_coverage_bbox(client):
    # BBox outside Seoul service area
    res = client.get("/api/v1/grid", params={"bbox": "128.5,35.5,128.6,35.6", "time_slot": "night"})
    assert res.status_code == 404
    _assert_error_envelope(res.json(), "out_of_coverage")


def test_404_unknown_endpoint(client):
    res = client.get("/api/v1/does_not_exist")
    assert res.status_code == 404
    _assert_error_envelope(res.json(), "not_found")


def test_422_validation_error_sensitivity_range(client):
    res = client.get(
        "/api/v1/grid",
        params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "night", "sound_sensitivity": 10},
    )
    assert res.status_code == 422
    data = res.json()
    _assert_error_envelope(data, "validation_error")
    assert isinstance(data["error"]["details"], list)


def test_422_validation_error_invalid_time_slot(client):
    res = client.get(
        "/api/v1/grid",
        params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "noon"},
    )
    assert res.status_code == 422
    _assert_error_envelope(res.json(), "validation_error")


def test_503_data_unavailable_envelope(client):
    """When a provider raises ApiError(ErrorCode.data_unavailable), it returns 503 with error envelope."""
    class UnavailableProvider(DataProvider):
        data_mode = "live"

        def get_cells(self, grid_ids, time_slot):
            raise ApiError(ErrorCode.data_unavailable, "Live database is currently synchronizing", {"retry_after_s": 30})

        def get_cell_detail(self, grid_id, time_slot, lng, lat):
            raise ApiError(ErrorCode.data_unavailable, "Live database is currently synchronizing")

        def get_sources(self):
            return []

        def precomputed_slots(self):
            return []

        def check_database(self):
            return "error", "unavailable"

        def has_poi(self):
            return False

    app.dependency_overrides[get_provider] = lambda: UnavailableProvider()

    res = client.get("/api/v1/grid", params={"bbox": "126.97,37.55,127.00,37.58", "time_slot": "night"})
    assert res.status_code == 503
    _assert_error_envelope(res.json(), "data_unavailable")


class TestCorsContract:
    def test_cors_preflight_allowed_for_default_dev_origins(self, client):
        for origin in ("http://localhost:3000", "http://localhost:5173"):
            res = client.options(
                "/api/v1/grid",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Content-Type",
                },
            )
            assert res.status_code == 200
            assert res.headers.get("access-control-allow-origin") == origin
            allowed_methods = res.headers.get("access-control-allow-methods", "")
            assert "GET" in allowed_methods
            assert "OPTIONS" in allowed_methods
            # Contract explicitly forbids allow_credentials
            assert res.headers.get("access-control-allow-credentials") != "true"

    def test_cors_preflight_rejected_for_unknown_origin(self, client):
        unknown_origin = "http://malicious-third-party.com"
        res = client.options(
            "/api/v1/grid",
            headers={
                "Origin": unknown_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert res.headers.get("access-control-allow-origin") != unknown_origin
