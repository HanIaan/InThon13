"""Contract validation tests for all v1 API endpoints.

Validates that responses match the Pydantic models in app.contracts.api_models,
which define the frontend API contract.
"""
from __future__ import annotations

from app.contracts import api_models as m


def test_health_endpoint_contract(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()

    # Validate against contract model
    parsed = m.HealthResponse.model_validate(data)
    assert parsed.api_version == "v1"
    assert parsed.status in ("ok", "degraded")
    assert parsed.data_mode in ("mock", "live")
    assert parsed.database.status in ("connected", "disabled", "error")
    assert isinstance(parsed.data_ready, bool)
    assert parsed.server_time is not None


def test_grid_endpoint_contract(client):
    res = client.get(
        "/api/v1/grid",
        params={
            "bbox": "126.97,37.55,127.00,37.58",
            "time_slot": "night",
            "sound_sensitivity": 3,
            "light_sensitivity": 3,
            "crowd_sensitivity": 3,
        },
    )
    assert res.status_code == 200
    data = res.json()

    # Validate against contract model
    parsed = m.GridFeatureCollection.model_validate(data)
    assert parsed.type == "FeatureCollection"
    assert len(parsed.features) > 0

    for feat in parsed.features:
        assert feat.type == "Feature"
        assert feat.id == feat.properties.grid_id
        if feat.properties.comfort_score is not None:
            assert 0.0 <= feat.properties.comfort_score <= 100.0
        assert 0.0 <= feat.properties.confidence <= 1.0

    assert parsed.meta.api_version == "v1"
    assert parsed.meta.time_slot == "night"
    assert parsed.meta.cell_size_m == 200
    assert parsed.meta.cell_count == len(parsed.features)


def test_location_inspect_endpoint_contract(client):
    res = client.get(
        "/api/v1/location/inspect",
        params={
            "lat": 37.5665,
            "lng": 126.9780,
            "time_slot": "night",
            "sound_sensitivity": 4,
            "light_sensitivity": 2,
            "crowd_sensitivity": 3,
        },
    )
    assert res.status_code == 200
    data = res.json()

    # Validate against contract model
    parsed = m.LocationInspectResponse.model_validate(data)
    assert parsed.grid_id.startswith("SEL200-")
    assert parsed.grid_center is not None
    assert len(parsed.grid_center) == 2
    assert 0.0 <= parsed.confidence <= 1.0

    # Indicators contract
    for name, ind in [
        ("noise", parsed.indicators.noise),
        ("light", parsed.indicators.light),
        ("crowd", parsed.indicators.crowd),
    ]:
        assert ind.indicator.value == name
        if ind.score is not None:
            assert 0.0 <= ind.score <= 100.0

    # Notes must contain environmental disclaimer
    assert any("200m 격자의 환경 추정값" in n for n in parsed.notes)


def test_recommendations_bbox_contract(client):
    res = client.get(
        "/api/v1/recommendations",
        params={
            "mode": "housing",
            "time_slot": "evening",
            "bbox": "126.97,37.55,127.00,37.58",
            "limit": 5,
        },
    )
    assert res.status_code == 200
    data = res.json()

    parsed = m.RecommendationsResponse.model_validate(data)
    assert len(parsed.items) <= 5
    for idx, item in enumerate(parsed.items, start=1):
        assert item.rank == idx
        assert item.type in ("grid", "poi")
        assert 0.0 <= item.comfort_score <= 100.0
        assert item.grade is not None
        assert 0.0 <= item.confidence <= 1.0

    assert parsed.meta.mode == "housing"
    assert "bbox" in parsed.meta.search


def test_recommendations_center_contract(client):
    res = client.get(
        "/api/v1/recommendations",
        params={
            "mode": "rest",
            "time_slot": "day",
            "lat": 37.5665,
            "lng": 126.9780,
            "radius_m": 1000,
            "limit": 10,
        },
    )
    assert res.status_code == 200
    data = res.json()

    parsed = m.RecommendationsResponse.model_validate(data)
    assert parsed.meta.mode == "rest"
    assert "center" in parsed.meta.search
    for item in parsed.items:
        assert item.distance_m is not None
        assert item.distance_m <= 1000.0


def test_data_status_endpoint_contract(client):
    res = client.get("/api/v1/data/status")
    assert res.status_code == 200
    data = res.json()

    parsed = m.DataStatusResponse.model_validate(data)
    assert parsed.data_mode in ("mock", "live")
    assert len(parsed.sources) >= 3

    source_ids = {s.source_id.value for s in parsed.sources}
    assert "sdot" in source_ids
    assert "living_population" in source_ids
    assert "nasa_black_marble" in source_ids

    assert parsed.grid.crs == "EPSG:5179"
    assert parsed.grid.cell_size_m == 200
    assert parsed.grid.cell_count > 0


def test_openapi_schema_contract(client):
    """Verify that OpenAPI spec is available, registers all /api/v1 endpoints, and includes error schemas."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert spec["openapi"].startswith("3.")
    paths = spec.get("paths", {})
    required_routes = [
        "/api/v1/health",
        "/api/v1/grid",
        "/api/v1/location/inspect",
        "/api/v1/recommendations",
        "/api/v1/data/status",
    ]
    for route in required_routes:
        assert route in paths, f"Route {route} missing from OpenAPI schema"
        assert "get" in paths[route], f"GET method missing for {route}"
        get_op = paths[route]["get"]
        assert "200" in get_op["responses"]
        # Error responses defined in routes
        for err_code in ("400", "404", "422", "500", "503"):
            assert err_code in get_op["responses"]

    schemas = spec.get("components", {}).get("schemas", {})
    assert "ErrorResponse" in schemas
    assert "ErrorBody" in schemas
