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
    SyncChunkDataResponse,
    SyncChunkRequest,
    SyncChunkResponse,
    SyncChunkedExportResponse,
    SyncConflictResolveRequest,
    SyncConflictResponse,
    SyncExportRequest,
    SyncExportResponse,
    SyncImportRequest,
    SyncImportResponse,
)
from app.security.device_store import get_trusted_device, list_trusted_devices
from app.security.envelope import DecryptionError, EnvelopeError, SignatureVerificationError
from app.sync.engine import get_sync_engine
from app.sync.store import (
    delete_sync_transfer,
    get_sync_chunk,
    get_sync_chunks,
    get_sync_state,
    list_sync_conflicts,
    mark_export_chunk_downloaded,
    resolve_sync_conflict,
)

logger = logging.getLogger("lifeos.api.sync")

router = APIRouter(prefix="/api/sync", tags=["Cross-Device Sync & Conflict Resolution"])


def _enforce_device_scope(user: AuthenticatedUser, target_device_id: str | None) -> None:
    """When authenticated with a device-scoped token, ensure operations apply only to that device."""
    if user.role == "device" and user.user_id.startswith("device:"):
        auth_dev_id = user.user_id.split(":", 1)[1]
        if target_device_id and target_device_id != auth_dev_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Device token cannot act on behalf of another peer device.",
            )


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
    _enforce_device_scope(user, body.peer_device_id)
    engine = get_sync_engine()
    try:
        envelope, batch = engine.export_sync_package_with_metadata(
            peer_device_id=body.peer_device_id,
            workspace_id=body.workspace_id,
            user_id=user.user_id,
        )
        env_dict = envelope.to_dict()
        return SyncExportResponse(
            envelope=CryptographicEnvelopeSchema(**env_dict),
            batch_id=batch.batch_id,
            items_count=len(batch.items),
            manifest=batch.manifest,
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as exc:
        logger.error(f"Failed to export sync package: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Sync export failed: {exc}")


@router.post("/export/chunked", response_model=SyncChunkedExportResponse)
def export_sync_chunked_endpoint(
    body: SyncExportRequest,
    chunk_size: int = Query(2 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024),
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncChunkedExportResponse:
    """Export and stage an encrypted synchronization package in chunks for >10 MB or resumable transfer."""
    _enforce_device_scope(user, body.peer_device_id)
    engine = get_sync_engine()
    try:
        meta = engine.export_chunked_package(
            peer_device_id=body.peer_device_id,
            workspace_id=body.workspace_id,
            user_id=user.user_id,
            chunk_size=chunk_size,
        )
        return SyncChunkedExportResponse(
            transfer_id=meta["transfer_id"],
            batch_id=meta["batch_id"],
            peer_device_id=meta["peer_device_id"],
            total_bytes=meta["total_bytes"],
            total_chunks=meta["total_chunks"],
            total_sha256=meta["total_sha256"],
            items_count=meta["items_count"],
            manifest=meta["manifest"],
        )
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as exc:
        logger.error(f"Failed to export chunked sync package: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Chunked sync export failed: {exc}")


@router.get("/export/chunked/{transfer_id}/{chunk_index}", response_model=SyncChunkDataResponse)
def get_export_sync_chunk_endpoint(
    transfer_id: str,
    chunk_index: int,
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncChunkDataResponse:
    """Fetch a single staged export chunk by transfer_id and chunk_index."""
    engine = get_sync_engine()
    chunk = get_sync_chunk(transfer_id, chunk_index, direction="export", db_path=engine.db_path)
    if not chunk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Export chunk {chunk_index} for transfer '{transfer_id}' not found.",
        )

    target_peer_id = chunk.get("peer_device_id")
    _enforce_device_scope(user, target_peer_id)
    if target_peer_id:
        peer = get_trusted_device(target_peer_id)
        if not peer or peer.get("trust_status") != "paired":
            delete_sync_transfer(transfer_id, direction="export", db_path=engine.db_path)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Peer '{target_peer_id}' is not trusted or has been revoked.",
            )

    response = SyncChunkDataResponse(
        transfer_id=chunk["transfer_id"],
        chunk_index=int(chunk["chunk_index"]),
        total_chunks=int(chunk["total_chunks"]),
        payload_b64=chunk["payload_b64"],
        chunk_sha256=chunk["chunk_sha256"],
        total_sha256=chunk["total_sha256"] or "",
    )
    mark_export_chunk_downloaded(transfer_id, chunk_index, db_path=engine.db_path)
    return response


@router.post("/import/chunked", response_model=SyncChunkResponse)
def import_sync_chunked_endpoint(
    body: SyncChunkRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncChunkResponse:
    """Upload a single SHA-256 verified sync chunk; reassembles and imports on final chunk."""
    _enforce_device_scope(user, body.peer_device_id)
    engine = get_sync_engine()
    try:
        res = engine.import_chunked_package(
            transfer_id=body.transfer_id,
            chunk_index=body.chunk_index,
            total_chunks=body.total_chunks,
            payload_b64=body.payload_b64,
            chunk_sha256=body.chunk_sha256,
            total_sha256=body.total_sha256,
            peer_device_id=body.peer_device_id,
            user_id=user.user_id,
        )
        report_schema = None
        if res.get("import_report") is not None:
            r = res["import_report"]
            report_schema = SyncImportResponse(
                session_id=r.session_id,
                peer_device_id=r.peer_device_id,
                items_imported=r.items_imported,
                items_skipped=r.items_skipped,
                conflicts_detected=r.conflicts_detected,
                conflicts_resolved=r.conflicts_resolved,
                success=r.success,
                timestamp=r.timestamp,
            )
        return SyncChunkResponse(
            transfer_id=res["transfer_id"],
            chunk_index=res["chunk_index"],
            total_chunks=res["total_chunks"],
            received_chunks=res["received_chunks"],
            completed=res["completed"],
            sha256_verified=res["sha256_verified"],
            import_report=report_schema,
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
        logger.error(f"Failed to import chunked sync package: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Chunked sync import failed: {exc}")


@router.get("/import/chunked/{transfer_id}", response_model=dict)
def get_import_chunked_status_endpoint(
    transfer_id: str,
    user: AuthenticatedUser = Depends(require_auth),
) -> dict:
    """Query received chunk indices for an in-progress chunked import transfer (for resume)."""
    engine = get_sync_engine()
    chunks = get_sync_chunks(transfer_id, direction="import", db_path=engine.db_path)
    return {
        "transfer_id": transfer_id,
        "total_chunks": int(chunks[0]["total_chunks"]) if chunks else 0,
        "received_indices": [int(c["chunk_index"]) for c in chunks],
        "received_count": len(chunks),
    }


@router.post("/import", response_model=SyncImportResponse)
def import_sync_package_endpoint(
    body: SyncImportRequest,
    user: AuthenticatedUser = Depends(require_auth),
) -> SyncImportResponse:
    """Ingest, decrypt, and apply an encrypted synchronization package received from a trusted peer."""
    _enforce_device_scope(user, body.envelope.sender_device_id)
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
