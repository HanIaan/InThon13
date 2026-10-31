"""FastAPI application entrypoint: `uvicorn app.main:app` from backend/."""
from __future__ import annotations

import logging

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

    app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
