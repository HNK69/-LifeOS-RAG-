"""
connectors.py

API routes for managing and syncing LifeOS data source connectors.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import require_admin, require_auth
from app.connectors.models import ConnectorStatus, SyncResult
from app.connectors.registry import get_connector, list_connectors, sync_connector

router = APIRouter(prefix="/api/connectors", tags=["Connectors"])


@router.get(
    "",
    response_model=list[ConnectorStatus],
    summary="List Registered Connectors",
    description="Returns all registered connectors and their operational status.",
)
async def get_connectors(_user=Depends(require_auth)):
    return list_connectors()


@router.get(
    "/{name}",
    response_model=ConnectorStatus,
    summary="Get Connector Details",
    description="Returns configuration and status for a specific connector.",
)
async def get_connector_details(name: str, _user=Depends(require_auth)):
    connector = get_connector(name)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{name}' not found",
        )
    return connector.get_status()


@router.post(
    "/{name}/sync",
    response_model=SyncResult,
    summary="Trigger Connector Synchronization",
    description="Synchronizes items from the specified connector into LifeOS.",
)
async def trigger_connector_sync(name: str, _user=Depends(require_auth)):
    connector = get_connector(name)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{name}' not found",
        )

    try:
        result = sync_connector(name)
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Connector synchronization failed: {exc}",
        )
