"""
health.py

Liveness and readiness probes for LifeOS Phase 11.
Zero LLM or embedding side-effects.
"""

from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.schemas import HealthResponse, ReadinessResponse

try:
    from config import DATA_DIR, DOCUMENTS_DIR, CHROMA_DIR, REGISTRY_DB
except ImportError:
    from app.config import DATA_DIR, DOCUMENTS_DIR, CHROMA_DIR, REGISTRY_DB

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Process Liveness Probe",
    description="Returns 200 when the LifeOS API process is alive and responding.",
)
async def health_check():
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/live",
    response_model=HealthResponse,
    summary="Minimal Liveness Probe",
    description="Returns 200 to verify the process is alive for container orchestrators.",
)
async def live_check():
    return HealthResponse(
        status="live",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Subsystem Readiness Probe",
    description="Verifies filesystem and local storage accessibility without triggering model loads or LLM calls.",
)
async def readiness_check():
    now_iso = datetime.now(timezone.utc).isoformat()
    checks = {}

    # 1. Check data directory
    try:
        data_path = Path(DATA_DIR)
        data_path.mkdir(parents=True, exist_ok=True)
        checks["data_directory"] = data_path.is_dir()
    except Exception:
        checks["data_directory"] = False

    # 2. Check documents directory
    try:
        docs_path = Path(DOCUMENTS_DIR)
        docs_path.mkdir(parents=True, exist_ok=True)
        checks["documents_directory"] = docs_path.is_dir()
    except Exception:
        checks["documents_directory"] = False

    # 3. Check SQLite storage availability
    try:
        from app.storage.sqlite import connect_sqlite
        reg_path = Path(REGISTRY_DB)
        with connect_sqlite(reg_path, timeout=1.0) as conn:
            conn.execute("SELECT 1")
        checks["sqlite_storage"] = True
    except Exception:
        checks["sqlite_storage"] = False

    # 4. Check Chroma storage directory accessibility
    try:
        chroma_path = Path(CHROMA_DIR)
        chroma_path.mkdir(parents=True, exist_ok=True)
        checks["chroma_directory"] = chroma_path.is_dir()
    except Exception:
        checks["chroma_directory"] = False

    all_ok = all(checks.values())
    overall_status = "ready" if all_ok else "unready"
    http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall_status,
            "checks": checks,
            "timestamp": now_iso,
        },
    )
