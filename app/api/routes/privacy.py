"""
privacy.py

REST API endpoints for the LifeOS Privacy Dashboard, Granular Consent Management,
Cryptographic Audit Trail, Self-Service Data Export, and Right-to-be-Forgotten Data Purge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import AuthenticatedUser
from app.api.dependencies import require_admin, require_auth
from app.api.schemas import (
    AuditLogEntryResponse,
    AuditVerifyResponse,
    ConsentGrantRequest,
    ConsentGrantResponse,
    ConsentRevokeRequest,
    PrivacyExportResponse,
    PrivacyForgetRequest,
    PrivacyForgetResponse,
    PrivacySummaryResponse,
)
from app.security.audit import query_audit_log, record_audit_event, verify_audit_log_integrity
from app.security.consent import (
    get_consent,
    grant_consent,
    list_consents,
    revoke_consent,
)
from app.security.device_identity import get_device_identity

logger = logging.getLogger("lifeos.api.privacy")

router = APIRouter()


@router.get(
    "/summary",
    response_model=PrivacySummaryResponse,
    summary="Get Privacy Dashboard Summary",
    description="Returns high-level privacy posture, consented locations count, telemetry status, and audit chain integrity.",
)
def get_privacy_summary(user: AuthenticatedUser = Depends(require_auth)):
    device = get_device_identity()
    all_consents = list_consents()
    active_consents = [c for c in all_consents if c.status == "active"]

    # Verify audit chain integrity
    is_valid, record_count, _ = verify_audit_log_integrity()

    # Document count
    try:
        from app.knowledge.document_registry import get_all_documents
        docs = get_all_documents()
        total_docs = len(docs)
    except Exception:
        total_docs = 0

    return PrivacySummaryResponse(
        device_id=device.device_id,
        device_name=device.device_name,
        local_first_mode=True,
        cloud_sync_enabled=False,
        telemetry_enabled=False,
        active_consented_sources=len(active_consents),
        total_consent_grants=len(all_consents),
        total_audit_events=record_count,
        audit_chain_valid=is_valid,
        total_indexed_documents=total_docs,
    )


@router.get(
    "/consent",
    response_model=list[ConsentGrantResponse],
    summary="List Granular Consent Grants",
    description="Returns user consent grants, optionally filtered by status ('active', 'revoked').",
)
def list_consent_grants(
    status_filter: str | None = Query(None, alias="status", description="Filter by status ('active', 'revoked')"),
    user: AuthenticatedUser = Depends(require_auth),
):
    grants = list_consents(status_filter=status_filter)
    return [ConsentGrantResponse(**g.to_dict()) for g in grants]


@router.post(
    "/consent",
    response_model=ConsentGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant Granular Consent",
    description="Explicitly authorizes LifeOS to access and index a specific local folder, file, or data connector.",
)
def create_consent_grant(
    req: ConsentGrantRequest,
    user: AuthenticatedUser = Depends(require_auth),
):
    grant = grant_consent(
        resource_path=req.resource_path,
        scope_type=req.scope_type,
        categories=req.categories,
        purposes=req.purposes,
        allow_cloud_sync=req.allow_cloud_sync,
        expires_at=req.expires_at,
        actor=user.user_id,
    )
    return ConsentGrantResponse(**grant.to_dict())


@router.delete(
    "/consent/{consent_id}",
    summary="Revoke Granular Consent",
    description="Revokes an active consent grant, immediately prohibiting future file access, scans, and indexing.",
)
def delete_consent_grant(
    consent_id: str,
    req: ConsentRevokeRequest | None = None,
    user: AuthenticatedUser = Depends(require_auth),
):
    grant = get_consent(consent_id)
    if not grant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Consent grant '{consent_id}' not found.",
        )

    reason = req.reason if req else "User requested revocation"
    revoked = revoke_consent(consent_id, reason=reason, actor=user.user_id)
    return {"revoked": revoked, "consent_id": consent_id, "status": "revoked"}


@router.get(
    "/audit",
    response_model=list[AuditLogEntryResponse],
    summary="Query Privacy Audit Log",
    description="Returns tamper-evident cryptographic audit records of data access and privacy operations.",
)
def get_audit_trail(
    event_type: str | None = Query(None, description="Filter by event type"),
    actor: str | None = Query(None, description="Filter by actor"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_auth),
):
    entries = query_audit_log(
        event_type=event_type,
        actor=actor,
        limit=limit,
        offset=offset,
    )
    return [AuditLogEntryResponse(**e) for e in entries]


@router.get(
    "/audit/verify",
    response_model=AuditVerifyResponse,
    summary="Verify Audit Trail Cryptographic Integrity",
    description="Recomputes and verifies the complete SHA-256 hash chain across all audit log entries.",
)
def verify_audit_trail(user: AuthenticatedUser = Depends(require_auth)):
    is_valid, count, err = verify_audit_log_integrity()
    return AuditVerifyResponse(
        valid=is_valid,
        verified_records=count,
        error=err,
    )


@router.post(
    "/export",
    response_model=PrivacyExportResponse,
    summary="Self-Service User Data Export (GDPR)",
    description="Returns a complete export bundle containing all local user data, metadata, context, and audit logs.",
)
def export_user_data(user: AuthenticatedUser = Depends(require_auth)):
    device = get_device_identity()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Consents
    consents = [c.to_dict() for c in list_consents()]

    # 2. Document metadata
    docs = []
    try:
        from app.knowledge.document_registry import get_all_documents
        docs = get_all_documents()
    except Exception:
        pass

    # 3. Context memories
    memories = []
    try:
        from app.context.context_store import get_context_store
        store = get_context_store()
        all_ctx = store.get_active_context()
        memories = [m.to_dict() for m in all_ctx]
    except Exception:
        pass

    # 4. People
    people = []
    try:
        from app.knowledge.people import get_all_people
        people = get_all_people()
    except Exception:
        pass

    # 5. Audit log
    audit_entries = query_audit_log(limit=500)

    # Log export event
    record_audit_event(
        event_type="data_export",
        actor=user.user_id,
        resource="user_data_export_bundle",
        purpose="compliance_export",
        status="success",
        details={
            "documents_count": len(docs),
            "memories_count": len(memories),
            "people_count": len(people),
        },
    )

    return PrivacyExportResponse(
        exported_at=now_iso,
        device_id=device.device_id,
        consents=consents,
        documents=docs,
        context_memories=memories,
        people=people,
        audit_log=audit_entries,
    )


@router.post(
    "/forget",
    response_model=PrivacyForgetResponse,
    summary="Right-to-be-Forgotten Data Purge",
    description="Cascading deletion of documents, vectors, people, or context records based on user command.",
)
def purge_user_data(
    req: PrivacyForgetRequest,
    user: AuthenticatedUser = Depends(require_admin),
):
    purged_docs = 0
    purged_ctx = 0
    purged_people = 0

    if req.target_type == "path":
        if not req.target_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_value path is required when target_type is 'path'.",
            )
        target_path = Path(req.target_value).resolve()

        # Find matching documents in registry
        try:
            from app.knowledge.document_registry import get_all_documents
            from app.ingestion.ingest import remove_file
            docs = get_all_documents()
            for d in docs:
                p = Path(d["path"]).resolve()
                if p == target_path or p.is_relative_to(target_path):
                    remove_file(p)
                    purged_docs += 1
        except Exception as exc:
            logger.error(f"Error purging path {target_path}: {exc}")

        msg = f"Purged {purged_docs} documents and associated vector chunks under '{target_path}'."

    elif req.target_type == "person":
        if not req.target_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_value person name is required when target_type is 'person'.",
            )
        try:
            from app.knowledge.people import delete_person
            deleted = delete_person(req.target_value)
            if deleted:
                purged_people = 1
        except Exception as exc:
            logger.error(f"Error purging person {req.target_value}: {exc}")

        msg = f"Purged person record for '{req.target_value}'."

    elif req.target_type == "all":
        if not req.confirm_permanent_purge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="confirm_permanent_purge must be True to execute complete data purge.",
            )

        # Clear document registry & vectors
        try:
            from app.knowledge.document_registry import get_all_documents
            from app.ingestion.ingest import remove_file
            docs = get_all_documents()
            for d in docs:
                try:
                    remove_file(Path(d["path"]))
                    purged_docs += 1
                except Exception:
                    pass
        except Exception:
            pass

        # Clear context
        try:
            from app.context.context_store import get_context_store
            store = get_context_store()
            store.clear()
            purged_ctx = 1
        except Exception:
            pass

        msg = f"Complete privacy purge completed ({purged_docs} docs, context cleared)."

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported target_type '{req.target_type}'. Choose 'path', 'person', or 'all'.",
        )

    # Record purge audit event
    record_audit_event(
        event_type="data_purge",
        actor=user.user_id,
        resource=f"{req.target_type}:{req.target_value or '*'}",
        purpose="right_to_be_forgotten",
        status="success",
        details={
            "target_type": req.target_type,
            "target_value": req.target_value,
            "purged_documents": purged_docs,
            "purged_people": purged_people,
        },
    )

    return PrivacyForgetResponse(
        success=True,
        purged_documents_count=purged_docs,
        purged_context_count=purged_ctx,
        purged_people_count=purged_people,
        message=msg,
    )
