"""
audit.py

Tamper-evident cryptographic privacy audit trail for LifeOS.
Maintains an append-only, SHA-256 hash-chained log of all data access,
consent mutations, document scans, queries, and privacy purges.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.privacy_store import _get_db_path, initialize_privacy_store
from app.storage.sqlite import connect_sqlite

logger = logging.getLogger("lifeos.security.audit")

GENESIS_HASH = "0" * 64


@dataclass
class AuditEntry:
    """Represents a single immutable entry in the cryptographic audit log."""

    event_id: str
    timestamp: str
    event_type: str
    actor: str
    resource: str
    purpose: str
    status: str
    prev_hash: str
    record_hash: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_record_hash(
    prev_hash: str,
    event_id: str,
    timestamp: str,
    event_type: str,
    actor: str,
    resource: str,
    purpose: str,
    status: str,
    details_json: str,
) -> str:
    """Compute deterministic SHA-256 hash across all fields in the audit record."""
    payload = f"{prev_hash}|{event_id}|{timestamp}|{event_type}|{actor}|{resource}|{purpose}|{status}|{details_json}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_audit_event(
    event_type: str,
    actor: str,
    resource: str,
    purpose: str = "general",
    status: str = "success",
    details: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> AuditEntry:
    """
    Append an immutable event to the cryptographic audit chain in privacy.db.
    """
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    now_iso = datetime.now(timezone.utc).isoformat()
    event_id = f"aud_{uuid.uuid4().hex[:16]}"
    details_dict = details or {}
    details_str = json.dumps(details_dict, sort_keys=True)

    with connect_sqlite(target_path) as conn:
        # Retrieve the latest record_hash to link the chain
        last_row = conn.execute(
            "SELECT record_hash FROM privacy_audit_log ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

        prev_hash = last_row["record_hash"] if last_row else GENESIS_HASH

        record_hash = _compute_record_hash(
            prev_hash=prev_hash,
            event_id=event_id,
            timestamp=now_iso,
            event_type=event_type,
            actor=actor,
            resource=resource,
            purpose=purpose,
            status=status,
            details_json=details_str,
        )

        conn.execute("""
            INSERT INTO privacy_audit_log (
                event_id, timestamp, event_type, actor, resource,
                purpose, status, prev_hash, record_hash, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            now_iso,
            event_type,
            actor,
            resource,
            purpose,
            status,
            prev_hash,
            record_hash,
            details_str,
        ))

    entry = AuditEntry(
        event_id=event_id,
        timestamp=now_iso,
        event_type=event_type,
        actor=actor,
        resource=resource,
        purpose=purpose,
        status=status,
        prev_hash=prev_hash,
        record_hash=record_hash,
        details=details_dict,
    )
    logger.debug(f"Recorded audit event {event_id} ({event_type}) hash={record_hash[:12]}...")
    return entry


def query_audit_log(
    event_type: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve audit log entries in reverse chronological order."""
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    query = "SELECT * FROM privacy_audit_log WHERE 1=1"
    params: list[Any] = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    if actor:
        query += " AND actor = ?"
        params.append(actor)

    query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with connect_sqlite(target_path) as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("details_json"):
                try:
                    d["details"] = json.loads(d["details_json"])
                except Exception:
                    d["details"] = {}
            else:
                d["details"] = {}
            results.append(d)
        return results


def verify_audit_log_integrity(db_path: Path | str | None = None) -> tuple[bool, int, str | None]:
    """
    Verify the cryptographic integrity of the entire audit chain from genesis to head.
    Returns:
        (is_valid: bool, verified_records_count: int, error_message: str | None)
    """
    target_path = _get_db_path(db_path)
    initialize_privacy_store(target_path)

    with connect_sqlite(target_path) as conn:
        rows = conn.execute(
            "SELECT * FROM privacy_audit_log ORDER BY rowid ASC"
        ).fetchall()

        if not rows:
            return True, 0, None

        expected_prev_hash = GENESIS_HASH

        for idx, row in enumerate(rows):
            # 1. Verify prev_hash matches preceding record_hash
            if row["prev_hash"] != expected_prev_hash:
                err = f"Broken hash chain at row {idx} (event_id={row['event_id']}): expected prev_hash '{expected_prev_hash}', got '{row['prev_hash']}'"
                logger.error(err)
                return False, idx, err

            # 2. Recompute record_hash and verify
            computed_hash = _compute_record_hash(
                prev_hash=row["prev_hash"],
                event_id=row["event_id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                actor=row["actor"],
                resource=row["resource"],
                purpose=row["purpose"],
                status=row["status"],
                details_json=row["details_json"] or "{}",
            )

            if computed_hash != row["record_hash"]:
                err = f"Cryptographic tamper detected at row {idx} (event_id={row['event_id']}): computed '{computed_hash}' != stored '{row['record_hash']}'"
                logger.error(err)
                return False, idx, err

            expected_prev_hash = row["record_hash"]

    return True, len(rows), None
