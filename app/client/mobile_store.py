"""
mobile_store.py

Isolated encrypted SQLite storage for the LifeOS Mobile Companion Client
(`mobile_companion.db`). Uses PBKDF2HMAC-SHA256 + AES-256-GCM symmetric
envelopes (`seal_symmetric` / `open_symmetric`) for sensitive cached data
and outbox mutations, with zeroization support for remote wipe.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.device_identity import DeviceIdentity
from app.security.envelope import open_symmetric, seal_symmetric
from app.storage.sqlite import connect_sqlite

logger = logging.getLogger("lifeos.client.mobile_store")


class MobileCompanionStore:
    """
    Persistent encrypted local store for the mobile companion client.
    Maintains mobile identity metadata, pinned desktop trust keys,
    encrypted context & document caches, offline outbox mutations,
    processed batch idempotency ledger, and platform lifecycle checkpoints.
    """

    def __init__(
        self,
        db_path: Path | str,
        storage_secret: str | None = None,
    ):
        self.db_path = Path(db_path).resolve()
        self.key_file = self.db_path.with_suffix(".vault.key")
        self._storage_secret = storage_secret or self._load_or_create_secret()
        self.initialize()

    def _load_or_create_secret(self) -> str:
        if self.key_file.is_file():
            try:
                secret = self.key_file.read_text(encoding="utf-8").strip()
                if secret:
                    return secret
            except Exception:
                pass
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(32)
        self.key_file.write_text(secret, encoding="utf-8")
        try:
            os.chmod(self.key_file, 0o600)
        except Exception:
            pass
        return secret

    def _seal(self, payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True)
        return seal_symmetric(raw, secret=self._storage_secret).to_json()

    def _open(self, envelope_json: str) -> Any:
        raw_bytes = open_symmetric(json.loads(envelope_json), secret=self._storage_secret)
        return json.loads(raw_bytes.decode("utf-8"))

    def initialize(self) -> None:
        """Create all mobile companion SQLite tables and indices."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect_sqlite(self.db_path, wal_mode=True) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mobile_identity (
                    device_id TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    os_info TEXT NOT NULL,
                    signing_public_key TEXT NOT NULL,
                    exchange_public_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS trusted_desktop (
                    desktop_device_id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    signing_public_key TEXT NOT NULL,
                    exchange_public_key TEXT NOT NULL,
                    trust_status TEXT NOT NULL,
                    paired_at TEXT NOT NULL,
                    last_synced_at TEXT,
                    metadata_enc TEXT,
                    device_token_enc TEXT
                )
            """)

            td_cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(trusted_desktop)").fetchall()
            }
            if "device_token_enc" not in td_cols:
                conn.execute("ALTER TABLE trusted_desktop ADD COLUMN device_token_enc TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_context (
                    workspace_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_enc TEXT NOT NULL,
                    context_type TEXT NOT NULL DEFAULT 'general',
                    vector_clock_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_tombstone INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace_id, key)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_documents (
                    workspace_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    metadata_enc TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_tombstone INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace_id, entity_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox_mutations (
                    mutation_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    payload_enc TEXT NOT NULL,
                    vector_clock_json TEXT NOT NULL,
                    lamport_clock INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    desktop_device_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    last_synced_at TEXT,
                    vector_clock_json TEXT NOT NULL,
                    local_sequence INTEGER NOT NULL DEFAULT 0,
                    pending_transfer_json TEXT,
                    PRIMARY KEY (desktop_device_id, workspace_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_batches (
                    batch_id TEXT PRIMARY KEY,
                    sender_device_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lifecycle_state (
                    platform TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_only_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL
                )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_outbox_status ON outbox_mutations(status, next_retry_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_ctx_ws ON cached_context(workspace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mobile_doc_ws ON cached_documents(workspace_id)")

    # ------------------------------------------------------------------
    # Identity & Trusted Desktop Pinning
    # ------------------------------------------------------------------

    def save_mobile_identity(self, identity: DeviceIdentity) -> None:
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO mobile_identity (
                    device_id, device_name, device_type, os_info,
                    signing_public_key, exchange_public_key, created_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_name = excluded.device_name,
                    device_type = excluded.device_type,
                    os_info = excluded.os_info,
                    signing_public_key = excluded.signing_public_key,
                    exchange_public_key = excluded.exchange_public_key,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    identity.device_id,
                    identity.device_name,
                    identity.device_type,
                    identity.os_info,
                    identity.signing_public_key_b64,
                    identity.exchange_public_key_b64,
                    identity.created_at,
                    identity.last_seen_at,
                ),
            )

    def get_mobile_identity_record(self) -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute("SELECT * FROM mobile_identity LIMIT 1").fetchone()
            return dict(row) if row else None

    def pin_trusted_desktop(
        self,
        desktop_device_id: str,
        host: str,
        port: int,
        signing_public_key: str,
        exchange_public_key: str,
        metadata: dict[str, Any] | None = None,
        device_token: str | None = None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_enc = self._seal(metadata or {})
        token_enc = self._seal({"device_token": device_token}) if device_token else None
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trusted_desktop (
                    desktop_device_id, host, port, signing_public_key,
                    exchange_public_key, trust_status, paired_at, last_synced_at, metadata_enc, device_token_enc
                ) VALUES (?, ?, ?, ?, ?, 'paired', ?, NULL, ?, ?)
                ON CONFLICT(desktop_device_id) DO UPDATE SET
                    host = excluded.host,
                    port = excluded.port,
                    signing_public_key = excluded.signing_public_key,
                    exchange_public_key = excluded.exchange_public_key,
                    trust_status = 'paired',
                    paired_at = excluded.paired_at,
                    metadata_enc = excluded.metadata_enc,
                    device_token_enc = COALESCE(excluded.device_token_enc, trusted_desktop.device_token_enc)
                """,
                (
                    desktop_device_id,
                    host,
                    int(port),
                    signing_public_key,
                    exchange_public_key,
                    now_iso,
                    meta_enc,
                    token_enc,
                ),
            )

    def get_trusted_desktop(self, desktop_device_id: str | None = None) -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            if desktop_device_id:
                row = conn.execute(
                    "SELECT * FROM trusted_desktop WHERE desktop_device_id = ?",
                    (desktop_device_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM trusted_desktop WHERE trust_status = 'paired' ORDER BY paired_at DESC LIMIT 1"
                ).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT * FROM trusted_desktop ORDER BY paired_at DESC LIMIT 1"
                    ).fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("metadata_enc"):
                try:
                    data["metadata"] = self._open(data["metadata_enc"])
                except Exception:
                    data["metadata"] = {}
            else:
                data["metadata"] = {}
            if data.get("device_token_enc"):
                try:
                    tok_obj = self._open(data["device_token_enc"])
                    data["device_token"] = tok_obj.get("device_token") if isinstance(tok_obj, dict) else None
                except Exception:
                    data["device_token"] = None
            else:
                data["device_token"] = None
            return data

    def revoke_trusted_desktop(self, desktop_device_id: str) -> bool:
        with connect_sqlite(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE trusted_desktop SET trust_status = 'revoked', device_token_enc = NULL WHERE desktop_device_id = ?",
                (desktop_device_id,),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Encrypted Context & Document Cache
    # ------------------------------------------------------------------

    def upsert_cached_context(
        self,
        workspace_id: str,
        key: str,
        value: Any,
        context_type: str = "general",
        vector_clock: dict[str, int] | None = None,
        updated_at: str | None = None,
        is_tombstone: bool = False,
    ) -> None:
        now_iso = updated_at or datetime.now(timezone.utc).isoformat()
        value_enc = self._seal({"key": key, "value": value, "context_type": context_type})
        clock_json = json.dumps(vector_clock or {})
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cached_context (
                    workspace_id, key, value_enc, context_type,
                    vector_clock_json, updated_at, is_tombstone
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, key) DO UPDATE SET
                    value_enc = excluded.value_enc,
                    context_type = excluded.context_type,
                    vector_clock_json = excluded.vector_clock_json,
                    updated_at = excluded.updated_at,
                    is_tombstone = excluded.is_tombstone
                """,
                (workspace_id, key, value_enc, context_type, clock_json, now_iso, 1 if is_tombstone else 0),
            )

    def get_cached_context(self, workspace_id: str, key: str) -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM cached_context WHERE workspace_id = ? AND key = ?",
                (workspace_id, key),
            ).fetchone()
            if not row:
                return None
            if row["is_tombstone"]:
                return {
                    "workspace_id": row["workspace_id"],
                    "key": row["key"],
                    "value": None,
                    "context_type": row["context_type"],
                    "vector_clock": json.loads(row["vector_clock_json"]),
                    "updated_at": row["updated_at"],
                    "is_tombstone": True,
                }
            decrypted = self._open(row["value_enc"])
            return {
                "workspace_id": row["workspace_id"],
                "key": row["key"],
                "value": decrypted.get("value"),
                "context_type": row["context_type"],
                "vector_clock": json.loads(row["vector_clock_json"]),
                "updated_at": row["updated_at"],
                "is_tombstone": False,
            }

    def list_cached_context(self, workspace_id: str | None = None, include_tombstones: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM cached_context WHERE 1=1"
        params: list[Any] = []
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        if not include_tombstones:
            query += " AND is_tombstone = 0"
        query += " ORDER BY updated_at DESC"
        with connect_sqlite(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            out = []
            for r in rows:
                decrypted = self._open(r["value_enc"])
                out.append({
                    "workspace_id": r["workspace_id"],
                    "key": r["key"],
                    "value": None if r["is_tombstone"] else decrypted.get("value"),
                    "context_type": r["context_type"],
                    "vector_clock": json.loads(r["vector_clock_json"]),
                    "updated_at": r["updated_at"],
                    "is_tombstone": bool(r["is_tombstone"]),
                })
            return out

    def upsert_cached_document(
        self,
        workspace_id: str,
        entity_id: str,
        filename: str,
        file_hash: str,
        metadata: dict[str, Any],
        updated_at: str | None = None,
        is_tombstone: bool = False,
    ) -> None:
        now_iso = updated_at or datetime.now(timezone.utc).isoformat()
        meta_enc = self._seal(metadata)
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cached_documents (
                    workspace_id, entity_id, filename, file_hash,
                    metadata_enc, updated_at, is_tombstone
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, entity_id) DO UPDATE SET
                    filename = excluded.filename,
                    file_hash = excluded.file_hash,
                    metadata_enc = excluded.metadata_enc,
                    updated_at = excluded.updated_at,
                    is_tombstone = excluded.is_tombstone
                """,
                (workspace_id, entity_id, filename, file_hash, meta_enc, now_iso, 1 if is_tombstone else 0),
            )

    def get_cached_document(self, workspace_id: str, entity_id: str) -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM cached_documents WHERE workspace_id = ? AND entity_id = ?",
                (workspace_id, entity_id),
            ).fetchone()
            if not row:
                return None
            return {
                "workspace_id": row["workspace_id"],
                "entity_id": row["entity_id"],
                "filename": row["filename"],
                "file_hash": row["file_hash"],
                "metadata": self._open(row["metadata_enc"]),
                "updated_at": row["updated_at"],
                "is_tombstone": bool(row["is_tombstone"]),
            }

    def list_cached_documents(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM cached_documents WHERE is_tombstone = 0"
        params: list[Any] = []
        if workspace_id:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        query += " ORDER BY updated_at DESC"
        with connect_sqlite(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "workspace_id": r["workspace_id"],
                    "entity_id": r["entity_id"],
                    "filename": r["filename"],
                    "file_hash": r["file_hash"],
                    "metadata": self._open(r["metadata_enc"]),
                    "updated_at": r["updated_at"],
                    "is_tombstone": bool(r["is_tombstone"]),
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Offline Mutation Outbox
    # ------------------------------------------------------------------

    def enqueue_mutation(
        self,
        mutation_id: str,
        entity_type: str,
        entity_id: str,
        workspace_id: str,
        change_type: str,
        data: dict[str, Any],
        vector_clock: dict[str, int],
        lamport_clock: int = 1,
    ) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_enc = self._seal(data)
        clock_json = json.dumps(vector_clock)
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO outbox_mutations (
                    mutation_id, entity_type, entity_id, workspace_id,
                    change_type, payload_enc, vector_clock_json, lamport_clock,
                    status, retry_count, next_retry_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, NULL, ?, ?)
                """,
                (
                    mutation_id,
                    entity_type,
                    entity_id,
                    workspace_id,
                    change_type,
                    payload_enc,
                    clock_json,
                    lamport_clock,
                    now_iso,
                    now_iso,
                ),
            )
        return {
            "mutation_id": mutation_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "workspace_id": workspace_id,
            "change_type": change_type,
            "data": data,
            "vector_clock": vector_clock,
            "lamport_clock": lamport_clock,
            "status": "pending",
            "retry_count": 0,
            "created_at": now_iso,
        }

    def get_pending_mutations(self, due_before_iso: str | None = None, ignore_backoff: bool = False) -> list[dict[str, Any]]:
        now_iso = due_before_iso or datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            if ignore_backoff:
                rows = conn.execute(
                    "SELECT * FROM outbox_mutations WHERE status = 'pending' ORDER BY created_at ASC, lamport_clock ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM outbox_mutations
                    WHERE status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    ORDER BY created_at ASC, lamport_clock ASC
                    """,
                    (now_iso,),
                ).fetchall()
            out = []
            for r in rows:
                out.append({
                    "mutation_id": r["mutation_id"],
                    "entity_type": r["entity_type"],
                    "entity_id": r["entity_id"],
                    "workspace_id": r["workspace_id"],
                    "change_type": r["change_type"],
                    "data": self._open(r["payload_enc"]),
                    "vector_clock": json.loads(r["vector_clock_json"]),
                    "lamport_clock": int(r["lamport_clock"]),
                    "status": r["status"],
                    "retry_count": int(r["retry_count"]),
                    "next_retry_at": r["next_retry_at"],
                    "last_error": r["last_error"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
            return out

    def mark_mutation_sent(self, mutation_id: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                "UPDATE outbox_mutations SET status = 'synced', updated_at = ? WHERE mutation_id = ?",
                (now_iso, mutation_id),
            )

    def record_mutation_failure(
        self,
        mutation_id: str,
        error: str,
        next_retry_at: str,
    ) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                UPDATE outbox_mutations
                SET retry_count = retry_count + 1,
                    next_retry_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE mutation_id = ?
                """,
                (next_retry_at, error, now_iso, mutation_id),
            )
            row = conn.execute(
                "SELECT retry_count FROM outbox_mutations WHERE mutation_id = ?",
                (mutation_id,),
            ).fetchone()
            return int(row["retry_count"]) if row else 0

    # ------------------------------------------------------------------
    # Sync State, Idempotency & Lifecycle Checkpoints
    # ------------------------------------------------------------------

    def get_sync_state(self, desktop_device_id: str, workspace_id: str = "*") -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE desktop_device_id = ? AND workspace_id = ?",
                (desktop_device_id, workspace_id),
            ).fetchone()
            if not row:
                return None
            return {
                "desktop_device_id": row["desktop_device_id"],
                "workspace_id": row["workspace_id"],
                "last_synced_at": row["last_synced_at"],
                "vector_clock": json.loads(row["vector_clock_json"]),
                "local_sequence": int(row["local_sequence"]),
                "pending_transfer": json.loads(row["pending_transfer_json"]) if row["pending_transfer_json"] else None,
            }

    def update_sync_state(
        self,
        desktop_device_id: str,
        workspace_id: str = "*",
        vector_clock: dict[str, int] | None = None,
        local_sequence: int = 0,
        pending_transfer: dict[str, Any] | None = None,
        last_synced_at: str | None = None,
    ) -> None:
        now_iso = last_synced_at or datetime.now(timezone.utc).isoformat()
        clock_json = json.dumps(vector_clock or {})
        pending_json = json.dumps(pending_transfer) if pending_transfer is not None else None
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    desktop_device_id, workspace_id, last_synced_at,
                    vector_clock_json, local_sequence, pending_transfer_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(desktop_device_id, workspace_id) DO UPDATE SET
                    last_synced_at = excluded.last_synced_at,
                    vector_clock_json = excluded.vector_clock_json,
                    local_sequence = excluded.local_sequence,
                    pending_transfer_json = excluded.pending_transfer_json
                """,
                (desktop_device_id, workspace_id, now_iso, clock_json, local_sequence, pending_json),
            )

    def is_batch_processed(self, batch_id: str) -> bool:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return row is not None

    def mark_batch_processed(self, batch_id: str, sender_device_id: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_batches (batch_id, sender_device_id, processed_at) VALUES (?, ?, ?)",
                (batch_id, sender_device_id, now_iso),
            )

    def save_lifecycle_checkpoint(self, platform: str, state: dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO lifecycle_state (platform, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (platform, json.dumps(state), now_iso),
            )

    def load_lifecycle_checkpoint(self, platform: str) -> dict[str, Any] | None:
        with connect_sqlite(self.db_path) as conn:
            row = conn.execute(
                "SELECT state_json FROM lifecycle_state WHERE platform = ?",
                (platform,),
            ).fetchone()
            if not row:
                return None
            return json.loads(row["state_json"])

    def save_local_only_workspaces(self, workspace_ids: set[str]) -> None:
        """Persist local_only workspace IDs so air-gap boundaries survive app restart."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect_sqlite(self.db_path) as conn:
            conn.execute("DELETE FROM local_only_workspaces")
            for ws_id in sorted(workspace_ids):
                conn.execute(
                    "INSERT INTO local_only_workspaces (workspace_id, updated_at) VALUES (?, ?)",
                    (ws_id, now_iso),
                )

    def load_local_only_workspaces(self) -> set[str]:
        """Load persisted local_only workspace IDs from mobile_companion.db."""
        with connect_sqlite(self.db_path) as conn:
            rows = conn.execute("SELECT workspace_id FROM local_only_workspaces").fetchall()
            return {r["workspace_id"] for r in rows}

    def wipe_by_path_or_workspace(self, workspace_id: str | None = None, path_prefix: str | None = None) -> int:
        """Selectively purge cached documents/context/outbox for a forgotten path or workspace."""
        removed = 0
        with connect_sqlite(self.db_path) as conn:
            if workspace_id:
                for tbl in ("cached_context", "cached_documents", "outbox_mutations"):
                    cur = conn.execute(f"DELETE FROM {tbl} WHERE workspace_id = ?", (workspace_id,))
                    removed += cur.rowcount
            if path_prefix:
                norm_prefix = os.path.normcase(str(Path(path_prefix).resolve()))
                rows = conn.execute("SELECT workspace_id, entity_id, metadata_enc FROM cached_documents").fetchall()
                for r in rows:
                    try:
                        meta = self._open(r["metadata_enc"])
                        doc_path = meta.get("path") or meta.get("source_path") or r["entity_id"]
                        cand = os.path.normcase(str(Path(doc_path).resolve()))
                        if cand == norm_prefix or cand.startswith(norm_prefix + os.sep) or cand.startswith(norm_prefix + "/"):
                            cur = conn.execute(
                                "DELETE FROM cached_documents WHERE workspace_id = ? AND entity_id = ?",
                                (r["workspace_id"], r["entity_id"]),
                            )
                            removed += cur.rowcount
                    except Exception:
                        pass
        return removed

    def wipe_all(self) -> dict[str, int]:
        """
        Cryptographic zeroization & full purge of all local mobile companion tables
        and symmetric vault key material upon remote wipe or peer revocation.
        """
        counts: dict[str, int] = {}
        tables = [
            "mobile_identity",
            "trusted_desktop",
            "cached_context",
            "cached_documents",
            "outbox_mutations",
            "sync_state",
            "processed_batches",
            "lifecycle_state",
            "local_only_workspaces",
        ]
        with connect_sqlite(self.db_path) as conn:
            for tbl in tables:
                cur = conn.execute(f"DELETE FROM {tbl}")
                counts[tbl] = cur.rowcount
            conn.commit()
            try:
                conn.execute("VACUUM")
            except Exception:
                pass

        # Overwrite and unlink symmetric vault key
        if self.key_file.is_file():
            try:
                size = self.key_file.stat().st_size
                self.key_file.write_bytes(b"\x00" * max(size, 64))
                self.key_file.unlink()
            except Exception:
                pass
        self._storage_secret = ""
        return counts
