"""Common error envelope. COORDINATOR-OWNED.

Every non-2xx response body is: {"error": {"code", "message", "details"}}.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    invalid_parameter = "invalid_parameter"  # 400: semantically invalid (bad bbox order, ...)
    bbox_too_large = "bbox_too_large"        # 400: bbox covers too many grid cells
    validation_error = "validation_error"    # 422: type/range validation failed
    out_of_coverage = "out_of_coverage"      # 404: coordinate outside the Seoul service area
    not_found = "not_found"                  # 404: unknown route or resource
    data_unavailable = "data_unavailable"    # 503: data source / DB not ready
    internal_error = "internal_error"        # 500


ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.invalid_parameter: 400,
    ErrorCode.bbox_too_large: 400,
    ErrorCode.validation_error: 422,
    ErrorCode.out_of_coverage: 404,
    ErrorCode.not_found: 404,
    ErrorCode.data_unavailable: 503,
    ErrorCode.internal_error: 500,
}


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] | list[Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody = Field(..., examples=[{
        "code": "bbox_too_large",
        "message": "bbox covers 9120 grid cells; the limit is 6000. Zoom in.",
        "details": {"cell_count": 9120, "max_cells": 6000},
    }])


class ApiError(Exception):
    """Raise from any layer; the app's exception handler renders the envelope."""

    def __init__(self, code: ErrorCode, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def status_code(self) -> int:
        return ERROR_STATUS[self.code]

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(error=ErrorBody(code=self.code, message=self.message, details=self.details))
