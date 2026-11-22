"""
privacy_store.py

Persistent SQLite database for granular consent tracking, cryptographic
privacy audit logs, and local privacy posture settings in LifeOS (`data/privacy.db`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.storage.sqlite import connect_sqlite

try:
    from config import PRIVACY_DB
except ImportError:
    from app.config import PRIVACY_DB

logger = logging.getLogger("lifeos.security.privacy_store")


def _get_db_path(custom_path: Path | str | None = None) -> Path:
    if custom_path is not None:
        return Path(custom_path).resolve()
    try:
        from config import PRIVACY_DB
        return Path(PRIVACY_DB).resolve()
    except Exception:
        from app.config import PRIVACY_DB
        return Path(PRIVACY_DB).resolve()


def get_privacy_db_connection(db_path: Path | str | None = None):
    """Obtain a SQLite connection with row factories for privacy.db."""
    target_path = _get_db_path(db_path)
    return connect_sqlite(target_path, wal_mode=True)


def initialize_privacy_store(db_path: Path | str | None = None) -> None:
    """Initialize the consent, audit log, and privacy settings tables in SQLite."""
    target_path = _get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        # 1. Granular Consent Grants table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consent_grants (
                consent_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,         -- 'folder', 'file', 'data_category', 'connector'
                resource_path TEXT NOT NULL,      -- Resolved filesystem path or connector identifier
                categories_json TEXT NOT NULL,    -- JSON list of allowed categories e.g. ["document", "spreadsheet", "audio"]
                purposes_json TEXT NOT NULL,      -- JSON list of allowed purposes e.g. ["indexing", "retrieval", "reasoning"]
                allow_cloud_sync INTEGER DEFAULT 0, -- Strict 0 by default (privacy-first rule)
                granted_at TEXT NOT NULL,
                expires_at TEXT,
                status TEXT NOT NULL,             -- 'active', 'revoked', 'expired'
                revoked_at TEXT,
                revocation_reason TEXT
            )
        """)

        # 2. Tamper-Evident Cryptographic Audit Log table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS privacy_audit_log (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,          -- 'consent_grant', 'consent_revoke', 'file_scan', 'document_ingest', 'vector_query', 'data_export', 'data_purge'
                actor TEXT NOT NULL,               -- 'user', 'system_scanner', 'agent', 'api_client'
                resource TEXT NOT NULL,            -- Path, query term, or entity identifier
                purpose TEXT NOT NULL,             -- 'indexing', 'retrieval', 'compliance_export', etc.
                status TEXT NOT NULL,              -- 'success', 'denied', 'error'
                prev_hash TEXT NOT NULL,           -- SHA-256 of the prior audit record (hash chain)
                record_hash TEXT NOT NULL,         -- SHA-256 of (prev_hash + fields)
                details_json TEXT
            )
        """)

        # 3. Privacy Posture & Settings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS privacy_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_consent_status ON consent_grants(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_consent_resource ON consent_grants(resource_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON privacy_audit_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event_type ON privacy_audit_log(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON privacy_audit_log(actor)")
