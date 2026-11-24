"""
consent.py

Granular consent records and real-time permission evaluation for LifeOS.

Guarantees:
- Explicit consent grants for local folders, files, and data connectors
- Purpose-based and data-category boundaries
- Zero silent filesystem scanning
- Immediate revocation enforcement
- Synchronized cryptographic audit logging
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.audit import record_audit_event
from app.security.privacy_store import _get_db_path, initialize_privacy_store
from app.storage.sqlite import connect_sqlite

logger = logging.getLogger("lifeos.security.consent")


@dataclass
class ConsentGrant:
    """Represents an explicit, granular user consent grant."""

    consent_id: str
    scope_type: str        # 'folder', 'file', 'data_category', 'connector'
    resource_path: str     # Canonical normalized filesystem path or connector identifier
    categories: list[str]  # e.g. ["document", "spreadsheet", "audio", "image"]
    purposes: list[str]    # e.g. ["indexing", "retrieval", "reasoning", "local_embeddings"]
    allow_cloud_sync: bool # Strictly False by default
    granted_at: str
    expires_at: str | None
    status: str            # 'active', 'revoked', 'expired'
    revoked_at: str | None = None
    revocation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_path_str(path: Path | str) -> str:
    """Normalize a path for cross-platform case-insensitive comparison."""
    p = Path(path).resolve()
    return os.path.normcase(str(p))


def grant_consent(
    resource_path: Path | str,
    scope_type: str = "folder",
    categories: list[str] | None = None,
    purposes: list[str] | None = None,
    allow_cloud_sync: bool = False,
    expires_at: str | None = None,
    actor: str = "user",
    db_path: Path | str | None = None,
) -> ConsentGrant:
    """
    Grant explicit consent for a filesystem location or connector source.
    """
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    canonical_path = str(Path(resource_path).resolve()) if scope_type in ("folder", "file") else str(resource_path)
    consent_id = f"csnt_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    cats = categories or ["document", "spreadsheet", "audio", "image", "structured_data"]
    purs = purposes or ["indexing", "retrieval", "reasoning", "local_embeddings"]

    cats_json = json.dumps(cats)
    purs_json = json.dumps(purs)

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO consent_grants (
                consent_id, scope_type, resource_path, categories_json,
                purposes_json, allow_cloud_sync, granted_at, expires_at,
                status, revoked_at, revocation_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL)
        """, (
            consent_id,
            scope_type,
            canonical_path,
            cats_json,
            purs_json,
            1 if allow_cloud_sync else 0,
            now_iso,
            expires_at,
        ))

    grant = ConsentGrant(
        consent_id=consent_id,
        scope_type=scope_type,
        resource_path=canonical_path,
        categories=cats,
        purposes=purs,
        allow_cloud_sync=allow_cloud_sync,
        granted_at=now_iso,
        expires_at=expires_at,
        status="active",
    )

    # Cryptographic audit entry
    record_audit_event(
        event_type="consent_grant",
        actor=actor,
        resource=canonical_path,
        purpose="consent_management",
        status="success",
        details={
            "consent_id": consent_id,
            "scope_type": scope_type,
            "categories": cats,
            "purposes": purs,
            "allow_cloud_sync": allow_cloud_sync,
        },
        db_path=target_path,
    )

    logger.info(f"Granted consent {consent_id} for {scope_type} '{canonical_path}'")

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="privacy.consent_changed",
            producer="privacy",
            resource_path=canonical_path,
            payload={"action": "granted", "consent_id": consent_id, "scope_type": scope_type, "resource_path": canonical_path},
        ))
    except Exception:
        pass

    return grant


def revoke_consent(
    consent_id: str,
    reason: str | None = None,
    actor: str = "user",
    db_path: Path | str | None = None,
) -> bool:
    """
    Revoke an active consent grant, immediately denying future access or indexing.
    """
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    now_iso = datetime.now(timezone.utc).isoformat()
    reason_str = reason or "User requested revocation"

    with connect_sqlite(target_path) as conn:
        row = conn.execute(
            "SELECT resource_path, status FROM consent_grants WHERE consent_id = ?",
            (consent_id,),
        ).fetchone()

        if not row:
            logger.warning(f"Consent grant {consent_id} not found.")
            return False

        if row["status"] == "revoked":
            return True

        conn.execute("""
            UPDATE consent_grants
            SET status = 'revoked', revoked_at = ?, revocation_reason = ?
            WHERE consent_id = ?
        """, (now_iso, reason_str, consent_id))

        resource_path = row["resource_path"]

    # Cryptographic audit entry
    record_audit_event(
        event_type="consent_revoke",
        actor=actor,
        resource=resource_path,
        purpose="consent_management",
        status="success",
        details={"consent_id": consent_id, "reason": reason_str},
        db_path=target_path,
    )

    logger.info(f"Revoked consent {consent_id} for '{resource_path}'")

    try:
        from app.events.bus import get_event_bus
        from app.events.models import LifeOSEvent
        get_event_bus().publish_sync(LifeOSEvent(
            event_type="privacy.consent_changed",
            producer="privacy",
            resource_path=resource_path,
            payload={"action": "revoked", "consent_id": consent_id, "resource_path": resource_path, "reason": reason_str},
        ))
    except Exception:
        pass

    return True


def get_consent(consent_id: str, db_path: Path | str | None = None) -> ConsentGrant | None:
    """Retrieve a specific consent grant by ID."""
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    with connect_sqlite(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM consent_grants WHERE consent_id = ?",
            (consent_id,),
        ).fetchone()

        if not row:
            return None

        return ConsentGrant(
            consent_id=row["consent_id"],
            scope_type=row["scope_type"],
            resource_path=row["resource_path"],
            categories=json.loads(row["categories_json"]),
            purposes=json.loads(row["purposes_json"]),
            allow_cloud_sync=bool(row["allow_cloud_sync"]),
            granted_at=row["granted_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            revoked_at=row["revoked_at"],
            revocation_reason=row["revocation_reason"],
        )


def list_consents(
    status_filter: str | None = None,
    db_path: Path | str | None = None,
) -> list[ConsentGrant]:
    """List all consent grants, optionally filtered by status ('active', 'revoked', 'expired')."""
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    query = "SELECT * FROM consent_grants WHERE 1=1"
    params: list[Any] = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    query += " ORDER BY granted_at DESC"

    with connect_sqlite(target_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [
            ConsentGrant(
                consent_id=r["consent_id"],
                scope_type=r["scope_type"],
                resource_path=r["resource_path"],
                categories=json.loads(r["categories_json"]),
                purposes=json.loads(r["purposes_json"]),
                allow_cloud_sync=bool(r["allow_cloud_sync"]),
                granted_at=r["granted_at"],
                expires_at=r["expires_at"],
                status=r["status"],
                revoked_at=r["revoked_at"],
                revocation_reason=r["revocation_reason"],
            )
            for r in rows
        ]


def get_active_consented_paths(db_path: Path | str | None = None) -> list[Path]:
    """Retrieve all active filesystem paths granted for scanning."""
    grants = list_consents(status_filter="active", db_path=db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    paths = []
    for g in grants:
        if g.scope_type in ("folder", "file"):
            if g.expires_at and g.expires_at < now_iso:
                continue
            paths.append(Path(g.resource_path).resolve())
    return paths


def is_consent_granted(
    candidate_path: Path | str,
    purpose: str = "indexing",
    category: str | None = None,
    db_path: Path | str | None = None,
) -> bool:
    """
    Evaluate in real-time whether an access request to candidate_path is authorized
    under an active, non-expired ConsentGrant with matching purpose and category.
    """
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    cand = Path(candidate_path).resolve()
    cand_norm = Path(_normalize_path_str(cand))
    now_iso = datetime.now(timezone.utc).isoformat()

    grants = list_consents(status_filter="active", db_path=target_path)

    for g in grants:
        if g.expires_at and g.expires_at < now_iso:
            continue

        # Check purpose permission
        if purpose not in g.purposes and "*" not in g.purposes:
            continue

        # Check category permission if specified
        if category and category not in g.categories and "*" not in g.categories:
            continue

        if g.scope_type in ("folder", "file"):
            grant_norm = Path(_normalize_path_str(g.resource_path))
            if cand_norm == grant_norm or cand_norm.is_relative_to(grant_norm):
                return True
        elif g.scope_type in ("connector", "plugin"):
            if str(candidate_path) == g.resource_path:
                return True

    return False
