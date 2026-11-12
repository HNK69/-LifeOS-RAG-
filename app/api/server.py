"""
server.py

FastAPI application entry point for LifeOS Phase 11.
Mounts middleware, error handlers, and route controllers.
Executable with:
    uvicorn app.api.server:app --host 127.0.0.1 --port 8000
"""

import sys
from pathlib import Path

# Ensure repo root and app directory are on sys.path for standalone uvicorn runs
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_APP_DIR = _BASE_DIR / "app"

for _p in [str(_BASE_DIR), str(_APP_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

try:
    from config import (
        API_CORS_ORIGINS,
        API_ALLOWED_METHODS,
        API_ALLOWED_HEADERS,
        API_TRUSTED_HOSTS,
    )
except ImportError:
    from app.config import (
        API_CORS_ORIGINS,
        API_ALLOWED_METHODS,
        API_ALLOWED_HEADERS,
        API_TRUSTED_HOSTS,
    )

from app.api.errors import register_error_handlers
from app.api.middleware import (
    AuditLoggingMiddleware,
    RateLimitingMiddleware,
    RequestIDMiddleware,
    RequestSizeLimiterMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.routes import admin, context, documents, health, people, query


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup and shutdown management."""
    yield
    # Graceful shutdown of agent executor pool if active
    try:
        from app.agent.runner import shutdown_agent_executor
        shutdown_agent_executor(wait=False, cancel_futures=True)
    except Exception:
        pass


def create_app() -> FastAPI:
    """Application factory for the LifeOS Phase 11 API."""
    app = FastAPI(
        title="LifeOS API",
        description="Secure programmatic HTTP boundary for the LifeOS personal intelligence platform.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 1. Register centralized error handlers
    register_error_handlers(app)

    # 2. Add Middlewares (in reverse order: innermost added first, outermost added last)
    # Request body size limiter (innermost)
    app.add_middleware(RequestSizeLimiterMiddleware)
    # Rate limiting
    app.add_middleware(RateLimitingMiddleware)
    # Security response headers
    app.add_middleware(SecurityHeadersMiddleware)
    # Request ID tracking
    app.add_middleware(RequestIDMiddleware)
    # Audit Logging (wraps downstream handlers, rate limiting, and size rejection)
    app.add_middleware(AuditLoggingMiddleware)

    # Trusted hosts protection
    if API_TRUSTED_HOSTS and "*" not in API_TRUSTED_HOSTS:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=API_TRUSTED_HOSTS,
        )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=API_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=API_ALLOWED_METHODS,
        allow_headers=API_ALLOWED_HEADERS,
    )

    # 3. Mount Route Controllers
    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(documents.router)
    app.include_router(context.router)
    app.include_router(people.router)
    app.include_router(admin.router)

    return app


app = create_app()
