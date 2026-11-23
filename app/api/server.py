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
from app.api.routes import admin, connectors, context, device, documents, events, health, metrics, people, privacy, query, workspaces


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup and shutdown management for LifeOS."""
    # 1. Startup: setup logging format and optional background watcher
    try:
        import config
        log_fmt = getattr(config, "LOG_FORMAT", "text")
        watcher_enabled = getattr(config, "WATCHER_ENABLED", False)
    except ImportError:
        from app import config
        log_fmt = getattr(config, "LOG_FORMAT", "text")
        watcher_enabled = getattr(config, "WATCHER_ENABLED", False)

    try:
        from app.ops.logging import setup_logging
        setup_logging(log_fmt)
    except Exception:
        pass

    if watcher_enabled:
        try:
            from app.knowledge.watcher import start_background_watcher
            start_background_watcher(run_initial_ingest=False)
        except Exception:
            pass

    # Optional background queue worker
    try:
        import config
        queue_worker_enabled = getattr(config, "QUEUE_WORKER_ENABLED", False)
    except ImportError:
        from app import config
        queue_worker_enabled = getattr(config, "QUEUE_WORKER_ENABLED", False)

    if queue_worker_enabled:
        try:
            from app.ingestion.queue import start_queue_worker
            start_queue_worker()
        except Exception:
            pass

    # Device identity & cryptographic envelope initialization
    try:
        from app.security.device_identity import get_device_identity
        from app.security.device_store import initialize_identity_store, sync_local_identity
        initialize_identity_store()
        dev_id = get_device_identity()
        sync_local_identity(dev_id)
    except Exception:
        pass

    # Privacy store initialization
    try:
        from app.security.privacy_store import initialize_privacy_store
        initialize_privacy_store()
    except Exception:
        pass

    # Workspace store initialization
    try:
        from app.workspace.store import init_workspace_store
        init_workspace_store()
    except Exception:
        pass

    # Event bus initialization
    try:
        import asyncio
        from app.events.bus import get_event_bus
        bus = get_event_bus()
        bus.set_loop(asyncio.get_running_loop())
    except Exception:
        pass

    yield

    # 2. Graceful Shutdown
    # Stop background queue worker if running
    try:
        from app.ingestion.queue import stop_queue_worker
        stop_queue_worker(timeout=5.0)
    except Exception:
        pass

    # Stop background watcher if running
    try:
        from app.knowledge.watcher import stop_background_watcher
        stop_background_watcher(timeout=5.0)
    except Exception:
        pass

    # Shutdown agent executor pool cleanly
    try:
        from app.agent.runner import shutdown_agent_executor
        shutdown_agent_executor(wait=True, cancel_futures=True)
    except Exception:
        pass


def create_app() -> FastAPI:
    """Application factory for the LifeOS Phase 11 / Phase 12 API."""
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
    app.include_router(metrics.router)
    app.include_router(query.router)
    app.include_router(documents.router)
    app.include_router(context.router)
    app.include_router(people.router)
    app.include_router(admin.router)
    app.include_router(connectors.router)
    app.include_router(device.router, prefix="/api/device", tags=["device"])
    app.include_router(privacy.router, prefix="/api/privacy", tags=["privacy"])
    app.include_router(workspaces.router)
    app.include_router(events.router, prefix="/api/events", tags=["events"])
    app.add_api_websocket_route("/ws/events", events.websocket_event_endpoint)

    return app


app = create_app()
