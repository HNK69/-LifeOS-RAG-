"""
store.py

Persistent SQLite database for change tracking, vector clock states,
offline tombstones, and conflict logs (`data/sync.db`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.storage.sqlite import connect_sqlite
from app.sync.models import SyncChangeType, SyncConflict, SyncEntityType, SyncItem
from app.sync.vector_clock import VectorClock

try:
    from config import SYNC_DB
except ImportError:
    from app.config import SYNC_DB

logger = logging.getLogger("lifeos.sync.store")


def _get_db_path(custom_path: Path | str | None = None) -> Path:
    if custom_path is not None:
        return Path(custom_path).resolve()
    try:
        from config import SYNC_DB
        return Path(SYNC_DB).resolve()
    except Exception:
        from app.config import SYNC_DB
        return Path(SYNC_DB).resolve()


def initialize_sync_store(db_path: Path | str | None = None) -> None:
    """Initialize synchronization database tables, indices, and schema."""
    target_path = _get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with connect_sqlite(target_path, wal_mode=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_change_log (
                change_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                change_type TEXT NOT NULL,
                vector_clock_json TEXT NOT NULL,
                lamport_clock INTEGER NOT NULL DEFAULT 1,
                origin_device_id TEXT NOT NULL,
                data_json TEXT NOT NULL,
                data_hash TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                is_tombstone INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                peer_device_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                last_synced_at TEXT NOT NULL,
                peer_vector_clock_json TEXT NOT NULL,
                local_sequence INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (peer_device_id, workspace_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_conflicts (
                conflict_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                peer_device_id TEXT NOT NULL,
                local_data_json TEXT NOT NULL,
                remote_data_json TEXT NOT NULL,
                local_clock_json TEXT NOT NULL,
                remote_clock_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unresolved',
                resolution_strategy TEXT NOT NULL,
                resolved_value_json TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_entity ON sync_change_log(entity_type, entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_workspace ON sync_change_log(workspace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_change_timestamp ON sync_change_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conflict_status ON sync_conflicts(status, workspace_id)")


def _compute_hash(data: Dict[str, Any]) -> str:
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def record_sync_change(
    entity_type: SyncEntityType | str,
    entity_id: str,
    data: Dict[str, Any],
    workspace_id: str = "default",
    origin_device_id: str = "local",
    vector_clock: Optional[VectorClock | Dict[str, int]] = None,
    lamport_clock: int = 1,
    db_path: Path | str | None = None,
) -> SyncItem:
    """Record an entity creation or update in the sync change ledger."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    change_id = f"chg_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    type_str = entity_type.value if isinstance(entity_type, SyncEntityType) else str(entity_type)
    clock_dict = vector_clock.to_dict() if isinstance(vector_clock, VectorClock) else dict(vector_clock or {})
    data_hash = _compute_hash(data)

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO sync_change_log (
                change_id, entity_type, entity_id, workspace_id,
                change_type, vector_clock_json, lamport_clock,
                origin_device_id, data_json, data_hash, timestamp, is_tombstone
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            change_id,
            type_str,
            entity_id,
            workspace_id,
            SyncChangeType.UPSERT.value,
            json.dumps(clock_dict),
            lamport_clock,
            origin_device_id,
            json.dumps(data),
            data_hash,
            now_iso,
        ))

    return SyncItem(
        item_id=change_id,
        entity_type=SyncEntityType(type_str),
        entity_id=entity_id,
        workspace_id=workspace_id,
        change_type=SyncChangeType.UPSERT,
        vector_clock=clock_dict,
        lamport_clock=lamport_clock,
        origin_device_id=origin_device_id,
        data=data,
        data_hash=data_hash,
        timestamp=now_iso,
        is_tombstone=False,
    )


def record_sync_tombstone(
    entity_type: SyncEntityType | str,
    entity_id: str,
    workspace_id: str = "default",
    origin_device_id: str = "local",
    vector_clock: Optional[VectorClock | Dict[str, int]] = None,
    lamport_clock: int = 1,
    db_path: Path | str | None = None,
) -> SyncItem:
    """Record an entity deletion tombstone to ensure offline deletion propagation."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    change_id = f"tmb_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    type_str = entity_type.value if isinstance(entity_type, SyncEntityType) else str(entity_type)
    clock_dict = vector_clock.to_dict() if isinstance(vector_clock, VectorClock) else dict(vector_clock or {})

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO sync_change_log (
                change_id, entity_type, entity_id, workspace_id,
                change_type, vector_clock_json, lamport_clock,
                origin_device_id, data_json, data_hash, timestamp, is_tombstone
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 1)
        """, (
            change_id,
            type_str,
            entity_id,
            workspace_id,
            SyncChangeType.DELETE.value,
            json.dumps(clock_dict),
            lamport_clock,
            origin_device_id,
            json.dumps({"deleted": True, "entity_id": entity_id}),
            now_iso,
        ))

    return SyncItem(
        item_id=change_id,
        entity_type=SyncEntityType(type_str),
        entity_id=entity_id,
        workspace_id=workspace_id,
        change_type=SyncChangeType.DELETE,
        vector_clock=clock_dict,
        lamport_clock=lamport_clock,
        origin_device_id=origin_device_id,
        data={"deleted": True, "entity_id": entity_id},
        data_hash="",
        timestamp=now_iso,
        is_tombstone=True,
    )


def get_latest_changes(
    workspace_id: Optional[str] = None,
    since_timestamp: Optional[str] = None,
    db_path: Path | str | None = None,
) -> List[SyncItem]:
    """Retrieve unique latest entity changes and tombstones for synchronization."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    query = """
        SELECT * FROM sync_change_log
        WHERE change_id IN (
            SELECT change_id FROM (
                SELECT change_id, entity_type, entity_id, workspace_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY entity_type, entity_id, workspace_id
                           ORDER BY timestamp DESC, lamport_clock DESC
                       ) as rn
                FROM sync_change_log
            ) WHERE rn = 1
        )
    """
    params: List[Any] = []
    if workspace_id:
        query += " AND workspace_id = ?"
        params.append(workspace_id)
    if since_timestamp:
        query += " AND timestamp > ?"
        params.append(since_timestamp)

    query += " ORDER BY timestamp ASC, lamport_clock ASC"

    with connect_sqlite(target_path) as conn:
        rows = conn.execute(query, params).fetchall()
        items = []
        for r in rows:
            items.append(
                SyncItem(
                    item_id=r["change_id"],
                    entity_type=SyncEntityType(r["entity_type"]),
                    entity_id=r["entity_id"],
                    workspace_id=r["workspace_id"],
                    change_type=SyncChangeType(r["change_type"]),
                    vector_clock=json.loads(r["vector_clock_json"]),
                    lamport_clock=r["lamport_clock"],
                    origin_device_id=r["origin_device_id"],
                    data=json.loads(r["data_json"]),
                    data_hash=r["data_hash"],
                    timestamp=r["timestamp"],
                    is_tombstone=bool(r["is_tombstone"]),
                )
            )
        return items


def get_sync_state(
    peer_device_id: str,
    workspace_id: str = "default",
    db_path: Path | str | None = None,
) -> Dict[str, Any] | None:
    """Retrieve peer synchronization state."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    with connect_sqlite(target_path) as conn:
        row = conn.execute(
            "SELECT * FROM sync_state WHERE peer_device_id = ? AND workspace_id = ?",
            (peer_device_id, workspace_id),
        ).fetchone()
        if not row:
            return None
        return {
            "peer_device_id": row["peer_device_id"],
            "workspace_id": row["workspace_id"],
            "last_synced_at": row["last_synced_at"],
            "peer_vector_clock": json.loads(row["peer_vector_clock_json"]),
            "local_sequence": row["local_sequence"],
        }


def update_sync_state(
    peer_device_id: str,
    workspace_id: str,
    peer_vector_clock: Dict[str, int] | VectorClock,
    local_sequence: int = 0,
    db_path: Path | str | None = None,
) -> None:
    """Update or upsert synchronization state for a peer device."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    clock_dict = peer_vector_clock.to_dict() if isinstance(peer_vector_clock, VectorClock) else dict(peer_vector_clock)

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO sync_state (
                peer_device_id, workspace_id, last_synced_at,
                peer_vector_clock_json, local_sequence
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(peer_device_id, workspace_id) DO UPDATE SET
                last_synced_at = excluded.last_synced_at,
                peer_vector_clock_json = excluded.peer_vector_clock_json,
                local_sequence = excluded.local_sequence
        """, (
            peer_device_id,
            workspace_id,
            now_iso,
            json.dumps(clock_dict),
            local_sequence,
        ))


def record_sync_conflict(
    entity_type: str,
    entity_id: str,
    workspace_id: str,
    peer_device_id: str,
    local_data: Dict[str, Any],
    remote_data: Dict[str, Any],
    local_clock: Dict[str, int] | VectorClock,
    remote_clock: Dict[str, int] | VectorClock,
    resolution_strategy: str = "last_write_wins",
    resolved_value: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> SyncConflict:
    """Record a detected conflict in the persistent conflict ledger."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    conflict_id = f"cfl_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    l_clock = local_clock.to_dict() if isinstance(local_clock, VectorClock) else dict(local_clock)
    r_clock = remote_clock.to_dict() if isinstance(remote_clock, VectorClock) else dict(remote_clock)
    resolved_json = json.dumps(resolved_value) if resolved_value else None
    resolved_at = now_iso if resolved_value else None
    status = "resolved" if resolved_value else "unresolved"

    with connect_sqlite(target_path) as conn:
        conn.execute("""
            INSERT INTO sync_conflicts (
                conflict_id, entity_type, entity_id, workspace_id,
                peer_device_id, local_data_json, remote_data_json,
                local_clock_json, remote_clock_json, status,
                resolution_strategy, resolved_value_json,
                created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            conflict_id,
            entity_type,
            entity_id,
            workspace_id,
            peer_device_id,
            json.dumps(local_data),
            json.dumps(remote_data),
            json.dumps(l_clock),
            json.dumps(r_clock),
            status,
            resolution_strategy,
            resolved_json,
            now_iso,
            resolved_at,
        ))

    return SyncConflict(
        conflict_id=conflict_id,
        entity_type=entity_type,
        entity_id=entity_id,
        workspace_id=workspace_id,
        peer_device_id=peer_device_id,
        local_data=local_data,
        remote_data=remote_data,
        local_clock=l_clock,
        remote_clock=r_clock,
        status=status,
        resolution_strategy=resolution_strategy,
        resolved_value=resolved_value,
        created_at=now_iso,
        resolved_at=resolved_at,
    )


def resolve_sync_conflict(
    conflict_id: str,
    resolved_value: Dict[str, Any],
    db_path: Path | str | None = None,
) -> bool:
    """Mark a conflict as resolved with an explicit value."""
    target_path = _get_db_path(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()

    with connect_sqlite(target_path) as conn:
        res = conn.execute("""
            UPDATE sync_conflicts
            SET status = 'resolved',
                resolved_value_json = ?,
                resolved_at = ?
            WHERE conflict_id = ?
        """, (json.dumps(resolved_value), now_iso, conflict_id))
        return res.rowcount > 0


def list_sync_conflicts(
    status_filter: Optional[str] = None,
    workspace_id: Optional[str] = None,
    db_path: Path | str | None = None,
) -> List[SyncConflict]:
    """List recorded sync conflicts."""
    target_path = _get_db_path(db_path)
    initialize_sync_store(target_path)

    query = "SELECT * FROM sync_conflicts WHERE 1=1"
    params: List[Any] = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if workspace_id:
        query += " AND workspace_id = ?"
        params.append(workspace_id)

    query += " ORDER BY created_at DESC"

    with connect_sqlite(target_path) as conn:
        rows = conn.execute(query, params).fetchall()
        conflicts = []
        for r in rows:
            conflicts.append(
                SyncConflict(
                    conflict_id=r["conflict_id"],
                    entity_type=r["entity_type"],
                    entity_id=r["entity_id"],
                    workspace_id=r["workspace_id"],
                    peer_device_id=r["peer_device_id"],
                    local_data=json.loads(r["local_data_json"]),
                    remote_data=json.loads(r["remote_data_json"]),
                    local_clock=json.loads(r["local_clock_json"]),
                    remote_clock=json.loads(r["remote_clock_json"]),
                    status=r["status"],
                    resolution_strategy=r["resolution_strategy"],
                    resolved_value=json.loads(r["resolved_value_json"]) if r["resolved_value_json"] else None,
                    created_at=r["created_at"],
                    resolved_at=r["resolved_at"],
                )
            )
        return conflicts
