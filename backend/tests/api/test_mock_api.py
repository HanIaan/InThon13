"""Baseline contract tests for the mock API (coordinator baseline; api-engine worker extends)."""
from tests.conftest import NIGHT, SMALL_BBOX

ORIGIN = "http://localhost:5173"


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["data_mode"] == "mock" and body["database"]["status"] == "disabled"


def test_grid_is_valid_geojson(client):
    r = client.get("/api/v1/grid", params={"bbox": SMALL_BBOX, **NIGHT})
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and fc["features"]
    for f in fc["features"]:
        assert f["type"] == "Feature" and f["geometry"]["type"] == "Polygon"
        ring = f["geometry"]["coordinates"][0]
        assert ring[0] == ring[-1] and len(ring) == 5
        lng, lat = ring[0]
        assert 126 < lng < 128 and 37 < lat < 38  # [lng, lat] order
        p = f["properties"]
        assert set(p) == {"grid_id", "comfort_score", "grade", "noise_score", "light_score", "crowd_score",
                          "confidence", "data_status"}
        assert p["data_status"] == "mock"
        if p["comfort_score"] is not None:
            assert 0 <= p["comfort_score"] <= 100 and p["grade"] in ("A", "B", "C")


def test_sensitivity_changes_score(client):
    def scores(**sens):
        r = client.get("/api/v1/grid", params={"bbox": SMALL_BBOX, "time_slot": "night", **sens})
        return {f["id"]: f["properties"]["comfort_score"] for f in r.json()["features"]}

    a = scores(sound_sensitivity=5, light_sensitivity=0, crowd_sensitivity=0)
    b = scores(sound_sensitivity=0, light_sensitivity=0, crowd_sensitivity=5)
    assert a != b


def test_all_zero_sensitivity_scores_nothing(client):
    r = client.get("/api/v1/grid", params={"bbox": SMALL_BBOX, "time_slot": "night",
                                           "sound_sensitivity": 0, "light_sensitivity": 0, "crowd_sensitivity": 0})
    body = r.json()
    assert body["meta"]["weights"] == {"noise": 0.0, "light": 0.0, "crowd": 0.0}
    assert all(f["properties"]["comfort_score"] is None for f in body["features"])


def test_day_excludes_light(client):
    r = client.get("/api/v1/grid", params={"bbox": SMALL_BBOX, "time_slot": "day", "light_sensitivity": 5})
    fc = r.json()
    assert all(f["properties"]["light_score"] is None for f in fc["features"])
    assert fc["meta"]["weights"]["light"] == 0


def test_bbox_too_large(client):
    r = client.get("/api/v1/grid", params={"bbox": "126.76,37.41,127.19,37.72", "time_slot": "day"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bbox_too_large"


def test_bbox_bad_format(client):
    r = client.get("/api/v1/grid", params={"bbox": "1,2,3", "time_slot": "day"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_parameter"


def test_validation_error_envelope(client):
    r = client.get("/api/v1/grid", params={"bbox": SMALL_BBOX, "time_slot": "noon"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_inspect(client):
    r = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.978, **NIGHT})
    assert r.status_code == 200
    b = r.json()
    assert b["grid_id"].startswith("SEL200-")
    assert set(b["indicators"]) == {"noise", "light", "crowd"}
    assert b["indicators"]["noise"]["raw_unit"] == "dB"
    assert b["indicators"]["noise"]["nearby_sensors"]
    assert b["data_status"] == "mock"


def test_inspect_out_of_coverage(client):
    r = client.get("/api/v1/location/inspect", params={"lat": 35.1, "lng": 129.0, **NIGHT})
    assert r.status_code == 404 and r.json()["error"]["code"] == "out_of_coverage"


def test_recommendations_center(client):
    r = client.get("/api/v1/recommendations", params={"mode": "rest", "lat": 37.5665, "lng": 126.978,
                                                       "radius_m": 1000, "limit": 5, **NIGHT})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 5
    assert [i["rank"] for i in items] == [1, 2, 3, 4, 5]
    assert items[0]["comfort_score"] >= items[-1]["comfort_score"]
    assert all(i["distance_m"] <= 1000 for i in items)


def test_recommendations_requires_one_search_form(client):
    r = client.get("/api/v1/recommendations", params={"mode": "housing", "time_slot": "day"})
    assert r.status_code == 400


def test_data_status(client):
    r = client.get("/api/v1/data/status")
    assert r.status_code == 200
    ids = {s["source_id"] for s in r.json()["sources"]}
    assert ids == {"sdot", "living_population", "nasa_black_marble"}


def test_cors_preflight(client):
    r = client.options("/api/v1/grid", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_cors_rejects_unknown_origin(client):
    r = client.options("/api/v1/grid", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_unknown_route_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_openapi_and_docs(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
