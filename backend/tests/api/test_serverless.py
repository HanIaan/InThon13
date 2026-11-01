"""Serverless deployment (Vercel): CDN cache headers and the per-request live provider."""
from fastapi.testclient import TestClient

from app.api import deps
from app.config import Settings, get_settings
from app.main import create_app
from app.services.query_provider import QueryProvider

BBOX = "126.97,37.55,127.00,37.58"


def _client(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        return TestClient(create_app())
    finally:
        get_settings.cache_clear()


def test_data_responses_are_cacheable_when_enabled(monkeypatch):
    client = _client(monkeypatch, DATA_MODE="mock", CACHE_SECONDS="3600")
    grid = client.get("/api/v1/grid", params={"bbox": BBOX, "time_slot": "night"})
    assert grid.status_code == 200
    assert "s-maxage=3600" in grid.headers["cache-control"]
    inspect = client.get("/api/v1/location/inspect", params={"lat": 37.5665, "lng": 126.978, "time_slot": "night"})
    assert "s-maxage=3600" in inspect.headers["cache-control"]


def test_health_and_errors_are_never_cached(monkeypatch):
    client = _client(monkeypatch, DATA_MODE="mock", CACHE_SECONDS="3600")
    assert "cache-control" not in client.get("/api/v1/health").headers
    bad = client.get("/api/v1/grid", params={"bbox": "1,2,3", "time_slot": "night"})
    assert bad.status_code >= 400 and "cache-control" not in bad.headers


def test_no_cache_header_by_default(monkeypatch):
    client = _client(monkeypatch, DATA_MODE="mock")
    assert "cache-control" not in client.get("/api/v1/grid", params={"bbox": BBOX, "time_slot": "night"}).headers


def test_live_query_mode_selects_per_request_provider(monkeypatch):
    settings = Settings(data_mode="live", live_read="query", database_url="postgresql://unused", _env_file=None)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps.get_provider.cache_clear()
    try:
        provider = deps.get_provider()
        # Constructing it must not touch the database (serverless cold starts stay cheap).
        assert isinstance(provider, QueryProvider) and provider._conn is None
    finally:
        deps.get_provider.cache_clear()
