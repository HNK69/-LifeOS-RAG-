"""
context.py

Personal context management endpoints for LifeOS Phase 11.
Wraps existing app.context.context_store operations.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import require_auth
from app.api.schemas import (
    ContextDeleteResponse,
    ContextListResponse,
    ContextSetRequest,
)
from app.context.context_store import (
    delete_context,
    get_all_context,
    set_context,
)

router = APIRouter(prefix="/api/context", tags=["Context"])


@router.get(
    "",
    response_model=ContextListResponse,
    summary="List Personal Context",
    description="Returns all active personal context items currently stored.",
)
async def list_context(_user=Depends(require_auth)):
    items = get_all_context()
    return ContextListResponse(context=items, count=len(items))


@router.post(
    "",
    summary="Set Personal Context",
    description="Upserts a personal context key-value item with optional temporal validity window.",
)
async def create_or_update_context(
    body: ContextSetRequest,
    _user=Depends(require_auth),
):
    try:
        set_context(
            key=body.key,
            value=body.value,
            context_type=body.context_type,
            valid_from=body.valid_from,
            valid_until=body.valid_until,
        )
        return {
            "status": "success",
            "message": f"Context key '{body.key}' successfully set.",
            "key": body.key,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set context: {exc}",
        )


@router.delete(
    "/{key}",
    response_model=ContextDeleteResponse,
    summary="Delete Personal Context Item",
    description="Removes a specific context item by key.",
)
async def remove_context_item(
    key: str,
    _user=Depends(require_auth),
):
    deleted = delete_context(key)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context key '{key}' not found",
        )
    return ContextDeleteResponse(
        key=key,
        status="deleted",
        message=f"Context key '{key}' removed successfully.",
    )
