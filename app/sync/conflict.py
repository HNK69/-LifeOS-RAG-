"""
conflict.py

Deterministic conflict resolution strategies for Milestone 8:
- Last-Write-Wins (LWW) with deterministic tie-breaking for context entries
- Non-destructive document branching/forking to preserve concurrent edits
- Conflict ledger recording to guarantee zero unrecoverable data loss
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.sync.models import SyncConflict, SyncEntityType
from app.sync.store import record_sync_conflict

logger = logging.getLogger("lifeos.sync.conflict")


class ConflictResolver:
    """
    Evaluates concurrent entity modifications and applies deterministic,
    non-destructive resolution strategies.
    """

    def __init__(self, local_device_id: str = "local"):
        self.local_device_id = local_device_id

    def resolve_context_conflict(
        self,
        entity_id: str,
        workspace_id: str,
        peer_device_id: str,
        local_data: Dict[str, Any],
        remote_data: Dict[str, Any],
        local_clock: Dict[str, int],
        remote_clock: Dict[str, int],
        db_path: Path | str | None = None,
    ) -> Tuple[Dict[str, Any], SyncConflict]:
        """
        Resolve concurrent context updates via Last-Write-Wins (LWW) with deterministic tie-break.
        Preserves the losing state in sync_conflicts.
        """
        local_time = local_data.get("updated_at") or ""
        remote_time = remote_data.get("updated_at") or ""

        # Compare ISO timestamps
        if local_time > remote_time:
            winner = "local"
            winning_data = local_data
        elif remote_time > local_time:
            winner = "remote"
            winning_data = remote_data
        else:
            # Deterministic tie-breaker: lexicographical device ID
            if self.local_device_id >= peer_device_id:
                winner = "local"
                winning_data = local_data
            else:
                winner = "remote"
                winning_data = remote_data

        conflict = record_sync_conflict(
            entity_type=SyncEntityType.CONTEXT.value,
            entity_id=entity_id,
            workspace_id=workspace_id,
            peer_device_id=peer_device_id,
            local_data=local_data,
            remote_data=remote_data,
            local_clock=local_clock,
            remote_clock=remote_clock,
            resolution_strategy="last_write_wins",
            resolved_value=winning_data,
            db_path=db_path,
        )

        logger.info(
            f"Resolved concurrent context conflict for '{entity_id}' in workspace '{workspace_id}' "
            f"via LWW (Winner: {winner})"
        )
        return winning_data, conflict

    def resolve_document_conflict(
        self,
        entity_id: str,  # relative document path or filename
        workspace_id: str,
        peer_device_id: str,
        local_data: Dict[str, Any],
        remote_data: Dict[str, Any],
        local_clock: Dict[str, int],
        remote_clock: Dict[str, int],
        db_path: Path | str | None = None,
    ) -> Tuple[str, Dict[str, Any], SyncConflict]:
        """
        Resolve concurrent document modifications via non-destructive branching.
        Returns (forked_relative_path, forked_data, conflict_record).
        """
        local_hash = local_data.get("file_hash", "")
        remote_hash = remote_data.get("file_hash", "")

        # If hashes are identical, both peers produced identical content
        if local_hash and local_hash == remote_hash:
            conflict = record_sync_conflict(
                entity_type=SyncEntityType.DOCUMENT_META.value,
                entity_id=entity_id,
                workspace_id=workspace_id,
                peer_device_id=peer_device_id,
                local_data=local_data,
                remote_data=remote_data,
                local_clock=local_clock,
                remote_clock=remote_clock,
                resolution_strategy="identical_converged",
                resolved_value=local_data,
                db_path=db_path,
            )
            return entity_id, local_data, conflict

        # Divergent file contents: Fork the incoming document non-destructively
        p = Path(entity_id)
        stem = p.stem
        ext = p.suffix
        slug_dev = peer_device_id.replace("dev_", "")[:6]
        timestamp_slug = datetime.now().strftime("%Y%m%d%H%M%S")
        forked_rel_path = str(p.parent / f"{stem}.conflict-{slug_dev}-{timestamp_slug}{ext}").replace("\\", "/")

        forked_data = dict(remote_data)
        forked_data["filename"] = Path(forked_rel_path).name
        forked_data["relative_path"] = forked_rel_path
        forked_data["is_conflict_fork"] = True
        forked_data["fork_origin_entity_id"] = entity_id

        conflict = record_sync_conflict(
            entity_type=SyncEntityType.DOCUMENT_META.value,
            entity_id=entity_id,
            workspace_id=workspace_id,
            peer_device_id=peer_device_id,
            local_data=local_data,
            remote_data=remote_data,
            local_clock=local_clock,
            remote_clock=remote_clock,
            resolution_strategy="branch_fork",
            resolved_value=forked_data,
            db_path=db_path,
        )

        logger.warning(
            f"Concurrent document divergence on '{entity_id}'. Created non-destructive "
            f"conflict branch '{forked_rel_path}' to preserve all user modifications."
        )
        return forked_rel_path, forked_data, conflict

    def resolve_workspace_conflict(
        self,
        entity_id: str,
        workspace_id: str,
        peer_device_id: str,
        local_data: Dict[str, Any],
        remote_data: Dict[str, Any],
        local_clock: Dict[str, int],
        remote_clock: Dict[str, int],
        db_path: Path | str | None = None,
    ) -> Tuple[Dict[str, Any], SyncConflict]:
        """Converge workspace settings dictionaries and adopt newer metadata."""
        local_time = local_data.get("updated_at") or ""
        remote_time = remote_data.get("updated_at") or ""

        base = dict(local_data if local_time >= remote_time else remote_data)
        # Deep merge settings
        merged_settings = dict(local_data.get("settings", {}))
        merged_settings.update(remote_data.get("settings", {}))
        base["settings"] = merged_settings

        conflict = record_sync_conflict(
            entity_type=SyncEntityType.WORKSPACE.value,
            entity_id=entity_id,
            workspace_id=workspace_id,
            peer_device_id=peer_device_id,
            local_data=local_data,
            remote_data=remote_data,
            local_clock=local_clock,
            remote_clock=remote_clock,
            resolution_strategy="converge_settings",
            resolved_value=base,
            db_path=db_path,
        )
        return base, conflict
