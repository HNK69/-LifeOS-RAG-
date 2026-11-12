"""
middleware.py

Production-grade Starlette/FastAPI middleware for LifeOS Phase 11:
- Request ID generation & propagation (X-Request-ID)
- Strict security headers injection
- In-process sliding-window rate limiting with Retry-After header
- Request payload size protection (413 Payload Too Large)
- Redacted, structured audit logging
"""

import logging
import re
import threading
import time
import uuid
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

try:
    from config import (
        API_RATE_LIMIT,
        API_RATE_WINDOW_SECONDS,
        API_MAX_REQUEST_BYTES,
        API_LOG_REQUESTS,
    )
except ImportError:
    from app.config import (
        API_RATE_LIMIT,
        API_RATE_WINDOW_SECONDS,
        API_MAX_REQUEST_BYTES,
        API_LOG_REQUESTS,
    )

logger = logging.getLogger("lifeos.api.audit")
_REQUEST_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


# ============================================================
# 1. REQUEST ID MIDDLEWARE
# ============================================================

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Ensure every request has a unique identifier for observability and audit trails.
    Accepts safe client-provided X-Request-ID or generates a cryptographically secure UUID.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_req_id = request.headers.get("X-Request-ID")
        if client_req_id and _REQUEST_ID_REGEX.match(client_req_id.strip()):
            request_id = client_req_id.strip()
        else:
            request_id = uuid.uuid4().hex

        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ============================================================
# 2. SECURITY HEADERS MIDDLEWARE
# ============================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject defensive HTTP security headers into all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

        # Strict-Transport-Security is only sent over genuine HTTPS connections
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        if is_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response


# ============================================================
# 3. RATE LIMITING MIDDLEWARE
# ============================================================

class RateLimiter:
    """
    In-process sliding-window rate limiter with bounded memory cleanup.
    Keyed by client IP or Bearer token prefix.
    """

    def __init__(self, limit: int = 60, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.history: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        Check if key is within rate limits.
        Returns (is_allowed, retry_after_seconds).
        """
        try:
            import config
            limit = getattr(config, "API_RATE_LIMIT", self.limit)
            window = getattr(config, "API_RATE_WINDOW_SECONDS", self.window_seconds)
        except ImportError:
            limit = self.limit
            window = self.window_seconds

        now = time.time()
        window_start = now - window

        with self._lock:
            # Clean entries older than window
            timestamps = [t for t in self.history.get(key, []) if t > window_start]
            if not timestamps and key in self.history:
                self.history.pop(key, None)

            # Bounded memory safeguard: prune inactive keys when dictionary grows large
            if len(self.history) > 5000:
                stale_keys = [
                    k for k, ts in self.history.items()
                    if not ts or ts[-1] <= window_start
                ]
                for k in stale_keys:
                    self.history.pop(k, None)

            if len(timestamps) >= limit:
                oldest = timestamps[0]
                retry_after = int(oldest + window - now) + 1
                return False, max(1, retry_after)

            timestamps.append(now)
            self.history[key] = timestamps
            return True, 0

    def reset(self):
        """Clear rate limiter state (used in testing)."""
        with self._lock:
            self.history.clear()


rate_limiter = RateLimiter(
    limit=API_RATE_LIMIT,
    window_seconds=API_RATE_WINDOW_SECONDS,
)


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """Enforce request quotas and return 429 Too Many Requests upon exhaustion."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Exempt health and readiness checks from rate limiting
        if request.url.path in {"/health", "/ready", "/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)

        # Derive rate limit key from auth token or remote host
        auth = request.headers.get("Authorization")
        x_api_key = request.headers.get("X-API-Key")
        if auth and "Bearer " in auth:
            client_key = "token:" + auth.replace("Bearer ", "").strip()[:8]
        elif x_api_key:
            client_key = "token:" + x_api_key.strip()[:8]
        else:
            client_key = "ip:" + (request.client.host if request.client else "unknown")

        allowed, retry_after = rate_limiter.is_allowed(client_key)
        if not allowed:
            request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                        "request_id": request_id,
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-Request-ID": request_id,
                },
            )

        return await call_next(request)


# ============================================================
# 4. REQUEST SIZE LIMITING MIDDLEWARE
# ============================================================

class RequestSizeLimiterMiddleware(BaseHTTPMiddleware):
    """Block oversized request payloads before reading into memory (413 Payload Too Large)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            import config
            max_bytes = getattr(config, "API_MAX_REQUEST_BYTES", API_MAX_REQUEST_BYTES)
        except ImportError:
            max_bytes = API_MAX_REQUEST_BYTES

        content_length = request.headers.get("Content-Length")
        if content_length:
            try:
                length = int(content_length)
                if length > max_bytes:
                    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "payload_too_large",
                                "message": f"Request body exceeds maximum allowed size ({max_bytes} bytes)",
                                "request_id": request_id,
                            }
                        },
                        headers={"X-Request-ID": request_id},
                    )
            except ValueError:
                pass

        return await call_next(request)


# ============================================================
# 5. AUDIT LOGGING MIDDLEWARE
# ============================================================

class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Log structured API request metrics without recording confidential payloads or tokens."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start_time) * 1000, 2)

        if API_LOG_REQUESTS:
            request_id = getattr(request.state, "request_id", "-")
            client_host = request.client.host if request.client else "unknown"
            logger.info(
                "%s %s | status=%d | duration=%sms | client=%s | request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                client_host,
                request_id,
            )

        return response
