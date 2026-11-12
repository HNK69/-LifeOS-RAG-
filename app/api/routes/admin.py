"""
admin.py

Administrative endpoints for LifeOS Phase 11.
Requires elevated administrative authentication.
Reuses existing reindexing, snapshot, and coordination lifecycles.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import require_admin
from app.api.schemas import ReindexResponse, ReindexStatusResponse
from app.knowledge.coordination import is_reindexing_active
from app.knowledge.reindex import reindex

router = APIRouter(prefix="/api/admin", tags=["Administration"])


@router.get(
    "/reindex/status",
    response_model=ReindexStatusResponse,
    summary="Check Reindex Status",
    description="Returns whether an administrative reindex operation is currently running.",
)
async def get_reindex_status(_admin=Depends(require_admin)):
    active = is_reindexing_active()
    return ReindexStatusResponse(
        is_active=active,
        status="running" if active else "idle",
    )


@router.post(
    "/reindex",
    response_model=ReindexResponse,
    summary="Trigger Administrative Reindex",
    description="Executes full reindex with automated snapshot and rollback protection. Admin privileges required.",
)
async def trigger_reindex(_admin=Depends(require_admin)):
    if is_reindexing_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A reindex operation is already in progress.",
        )

    result = reindex(raise_on_error=False)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reindex failed: {result.get('error', 'Unknown failure')}",
        )

    return ReindexResponse(
        success=bool(result.get("success")),
        processed_count=int(result.get("processed_count", 0)),
        error_count=int(result.get("error_count", 0)),
        duration_seconds=float(result.get("duration_seconds", 0.0)),
    )
