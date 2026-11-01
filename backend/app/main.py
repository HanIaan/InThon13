"""FastAPI application entrypoint: `uvicorn app.main:app` from backend/."""
from __future__ import annotations

import logging
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.config import get_settings
from app.contracts.constants import API_PREFIX
from app.contracts.errors import ApiError, ErrorCode

log = logging.getLogger("app")


def _error(code: ErrorCode, message: str, details=None) -> JSONResponse:
    err = ApiError(code, message, details)
    return JSONResponse(status_code=err.status_code, content=err.to_response().model_dump(mode="json"))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="주거 감각지수 지도 API",
        version="1.0.0",
        description="서울 200m 격자 소음·빛공해·혼잡도 기반 개인화 감각지수 API. "
                    "응답의 data_status로 observed / estimated / mock / missing을 구분합니다.",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    # The CDN (Vercel) may keep successful data responses; the data only changes when a new
    # snapshot is published. Health/status and errors are never cached.
    cached_paths = tuple(f"{API_PREFIX}/{name}" for name in ("grid", "location/inspect", "recommendations"))

    @app.middleware("http")
    async def _cache_headers(request: Request, call_next):
        response = await call_next(request)
        if (settings.cache_seconds > 0 and request.method == "GET" and response.status_code == 200
                and request.url.path.startswith(cached_paths)):
            response.headers["Cache-Control"] = (
                f"public, max-age=60, s-maxage={settings.cache_seconds}, stale-while-revalidate=86400")
        return response

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _error(exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _error(ErrorCode.validation_error, "request parameters failed validation", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = ErrorCode.not_found if exc.status_code == 404 else ErrorCode.invalid_parameter
        if exc.status_code >= 500:
            code = ErrorCode.internal_error
        resp = _error(code, str(exc.detail))
        resp.status_code = exc.status_code
        return resp

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return _error(ErrorCode.internal_error, "internal server error")

    @app.on_event("startup")
    async def _warm_provider():
        # Live data loads from PostGIS/SQLite in tens of seconds; do it before the first map request.
        from app.api.deps import get_provider

        warm = getattr(get_provider(), "warm", None)
        if warm:
            threading.Thread(target=warm, name="provider-warm", daemon=True).start()

    app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
