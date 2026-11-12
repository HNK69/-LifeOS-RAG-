"""
errors.py

Centralized error handling and exception mappers for the LifeOS Phase 11 API.
Guarantees consistent JSON error schemas and strictly prevents stack traces,
database paths, or sensitive credentials from reaching the caller.
"""

import logging
import uuid
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("lifeos.api.errors")


def _get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def register_error_handlers(app: FastAPI) -> None:
    """Register all centralized exception handlers on the FastAPI app instance."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        req_id = _get_request_id(request)
        code_map = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "payload_too_large",
            422: "validation_error",
            429: "rate_limit_exceeded",
            500: "internal_server_error",
            503: "service_unavailable",
        }
        err_code = code_map.get(exc.status_code, "http_error")
        headers = dict(exc.headers or {})
        headers["X-Request-ID"] = req_id

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": err_code,
                    "message": str(exc.detail),
                    "request_id": req_id,
                }
            },
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        req_id = _get_request_id(request)
        # Summarize validation errors without leaking sensitive field content
        errors_summary = []
        for err in exc.errors():
            loc = " -> ".join(str(l) for l in err.get("loc", []))
            msg = err.get("msg", "Invalid value")
            errors_summary.append(f"{loc}: {msg}")

        detail_message = "; ".join(errors_summary) if errors_summary else "Request validation failed"

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": detail_message,
                    "request_id": req_id,
                }
            },
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        req_id = _get_request_id(request)
        logger.warning("ValueError on %s: %s | request_id=%s", request.url.path, exc, req_id)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": {
                    "code": "bad_request",
                    "message": str(exc),
                    "request_id": req_id,
                }
            },
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(FileNotFoundError)
    async def file_not_found_handler(request: Request, exc: FileNotFoundError):
        req_id = _get_request_id(request)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "not_found",
                    "message": "The requested resource or document was not found",
                    "request_id": req_id,
                }
            },
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        req_id = _get_request_id(request)
        logger.exception("Unhandled server exception on %s | request_id=%s: %s", request.url.path, req_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred while processing your request.",
                    "request_id": req_id,
                }
            },
            headers={"X-Request-ID": req_id},
        )
