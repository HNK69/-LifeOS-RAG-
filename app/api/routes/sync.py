"""
sync.py

REST API endpoints for Milestone 8: Cross-Device Synchronization,
Encrypted Package Export/Import, and Distributed Conflict Resolution.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import AuthenticatedUser
from app.api.dependencies import require_auth
from app.api.schemas import (
    CryptographicEnvelopeSchema,
    PeerSyncStatusResponse,
    SyncConflictResolveRequest,
    SyncConflictResponse,
    SyncExportRequest,
    SyncExportResponse,
    SyncImportRequest,
    SyncImportResponse,
)
from app.security.device_store import list_trusted_devices
from app.security.envelope import DecryptionError, EnvelopeError, SignatureVerificationError
from app.sync.engine import get_sync_engine
from app.sync.store import get_sync_state, list_sync_conflicts, resolve_sync_conflict

logger = logging.getLogger("lifeos.api.sync")

router = APIRouter(prefix="/api/sync", tags=["Cross-Device Sync & Conflict Resolution"])


@router.get("/peers", response_model=List[PeerSyncStatusResponse])
def list_sync_peers(
    user: AuthenticatedUser = Depends(require_auth),
) -> List[PeerSyncStatusResponse]:
    """List paired peer devices and their synchronization statuses."""
    peers = list_trusted_devices()
    conflicts = list_sync_conflicts(status_filter="unresolved")
    results = []

    for p in peers:
        dev_id = p["device_id"]
        st = get_sync_state(dev_id, workspace_id="*") or get_sync_state(dev_id, workspace_id="default")
        peer_conflicts = [c for c in conflicts if c.peer_device_id == dev_id]

        results.append(
            PeerSyncStatusResponse(
                peer_device_id=dev_id,
                device_name=p["device_name"],
                device_type=p["device_type"],
                trust_status=p["trust_status"],
                last_synced_at=st["last_synced_at"] if st else None,
                unresolved_conflicts=len(peer_conflicts),
            )
        )
    return results


@router.post("/export", response_model=SyncExportResponse)
def export_sync_package_endpoint(
    body: SyncExportRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncExportResponse:
    """Export and seal an encrypted synchronization batch for a trusted peer device."""
    engine = get_sync_engine()
    try:
        envelope = engine.export_sync_package(
            peer_device_id=body.peer_device_id,
            workspace_id=body.workspace_id,
            user_id=user.user_id,
        )
        env_dict = envelope.to_dict()
        return SyncExportResponse(
            envelope=CryptographicEnvelopeSchema(**env_dict),
            batch_id=envelope.sender_device_id,
            items_count=1,
            manifest={"package": 1},
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as exc:
        logger.error(f"Failed to export sync package: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Sync export failed: {exc}")


@router.post("/import", response_model=SyncImportResponse)
def import_sync_package_endpoint(
    body: SyncImportRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncImportResponse:
    """Ingest, decrypt, and apply an encrypted synchronization package received from a trusted peer."""
    engine = get_sync_engine()
    try:
        report = engine.import_sync_package(
            envelope_data=body.envelope.model_dump(),
            user_id=user.user_id,
        )
        return SyncImportResponse(
            session_id=report.session_id,
            peer_device_id=report.peer_device_id,
            items_imported=report.items_imported,
            items_skipped=report.items_skipped,
            conflicts_detected=report.conflicts_detected,
            conflicts_resolved=report.conflicts_resolved,
            success=report.success,
            timestamp=report.timestamp,
        )
    except SignatureVerificationError as sve:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Cryptographic signature verification failed: {sve}")
    except DecryptionError as de:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cryptographic decryption failed: {de}")
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        logger.error(f"Failed to import sync package: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Sync import failed: {exc}")


@router.get("/conflicts", response_model=List[SyncConflictResponse])
def get_conflicts_endpoint(
    status_filter: Optional[str] = Query(None, alias="status"),
    workspace_id: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(require_auth),
) -> List[SyncConflictResponse]:
    """List detected synchronization conflicts."""
    conflicts = list_sync_conflicts(status_filter=status_filter, workspace_id=workspace_id)
    return [
        SyncConflictResponse(
            conflict_id=c.conflict_id,
            entity_type=c.entity_type,
            entity_id=c.entity_id,
            workspace_id=c.workspace_id,
            peer_device_id=c.peer_device_id,
            local_data=c.local_data,
            remote_data=c.remote_data,
            local_clock=c.local_clock,
            remote_clock=c.remote_clock,
            status=c.status,
            resolution_strategy=c.resolution_strategy,
            resolved_value=c.resolved_value,
            created_at=c.created_at,
            resolved_at=c.resolved_at,
        )
        for c in conflicts
    ]


@router.post("/conflicts/{conflict_id}/resolve", response_model=dict)
def resolve_conflict_endpoint(
    conflict_id: str,
    body: SyncConflictResolveRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> dict:
    """Manually resolve a synchronization conflict with an explicit value."""
    success = resolve_sync_conflict(conflict_id, body.resolved_value)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Conflict '{conflict_id}' not found.")

    # If context conflict, apply resolved value
    conflicts = list_sync_conflicts()
    c = next((item for item in conflicts if item.conflict_id == conflict_id), None)
    if c and c.entity_type == "context":
        from app.context.context_store import set_context
        set_context(
            key=c.entity_id,
            value=body.resolved_value.get("value", ""),
            context_type=body.resolved_value.get("context_type", "general"),
            workspace_id=c.workspace_id,
        )

    return {"conflict_id": conflict_id, "resolved": True, "status": "resolved"}
