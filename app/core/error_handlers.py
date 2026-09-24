from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import redact

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"success": False, "error": error}


def register_exception_handlers(app: FastAPI, settings: Settings) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("%s on %s %s: %s", exc.code, request.method, request.url.path, exc.message)
        else:
            logger.info("%s on %s %s", exc.code, request.method, request.url.path)
        details = redact(exc.details) if isinstance(exc.details, str) else exc.details
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, redact(exc.message), details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only loc/msg/type are returned: never echo the submitted input back.
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", "Invalid value"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request validation failed.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(PyMongoError)
    async def handle_mongo_error(request: Request, exc: PyMongoError) -> JSONResponse:
        logger.error(
            "MongoDB error on %s %s: %s", request.method, request.url.path, type(exc).__name__
        )
        return JSONResponse(
            status_code=503,
            content=error_body(
                "database_error", "The database is unavailable. Check that MongoDB is running."
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        details = None
        if settings.debug_enabled:
            details = {"type": type(exc).__name__, "message": redact(str(exc))[:300]}
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected error occurred.", details),
        )
