from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .i18n import t


class ApiError(Exception):
    """Stable machine code + localized text + request_id."""

    def __init__(self, code: str, status: int = 400, details: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None, **params: Any):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details or {}
        self.headers = headers or {}
        self.params = params


def not_found(entity: str = "resource") -> ApiError:
    return ApiError("not_found", 404, entity=entity)


def forbidden() -> ApiError:
    return ApiError("forbidden", 403)


def request_locale(request: Request) -> str:
    return getattr(request.state, "locale", "ru")


def error_body(request: Request, code: str, status: int, details: dict | None = None, **params) -> JSONResponse:
    loc = request_locale(request)
    return JSONResponse(status_code=status, content={"error": {
        "code": code,
        "message": t(f"errors.{code}", loc, **params),
        "request_id": getattr(request.state, "request_id", None),
        "details": details or {},
    }})


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    resp = error_body(request, exc.code, exc.status, exc.details, **exc.params)
    resp.headers.update(exc.headers)
    return resp


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [{"loc": [str(x) for x in e.get("loc", [])], "type": e.get("type")} for e in exc.errors()]
    return error_body(request, "validation_failed", 422, {"fields": fields})
