"""Runtime settings from environment variables (.env supported). COORDINATOR-OWNED."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEV_ORIGINS = ["http://localhost:3000", "http://localhost:5173"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_mode: Literal["mock", "live"] = "mock"
    # live only: "snapshot" loads the whole published dataset into memory at startup (long-running
    # server); "query" reads just the requested cells per request (serverless, e.g. Vercel).
    live_read: Literal["snapshot", "query"] = "snapshot"
    # Seconds a CDN may cache data responses (s-maxage). 0 disables. Data changes only on republish.
    cache_seconds: int = 0
    cors_dev_origins: str = ""
    cors_prod_origins: str = ""
    # Ingestion output / precomputed indicator directory (relative paths resolve from backend/).
    processed_data_dir: str = "../data/processed"

    database_url: str | None = None
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    seoul_open_api_key: str | None = None
    nasa_earthdata_token: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        extra = [o.strip() for o in f"{self.cors_dev_origins},{self.cors_prod_origins}".split(",") if o.strip()]
        return list(dict.fromkeys(DEFAULT_DEV_ORIGINS + extra))


@lru_cache
def get_settings() -> Settings:
    return Settings()
