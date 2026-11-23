"""
engine.py

Central synchronization engine for Milestone 8:
- Asymmetric envelope encryption and digital signing for peer payloads
- Scoped workspace packaging with local_only air-gap enforcement
- Vector clock causality evaluation and tombstone deletion propagation
- Non-destructive conflict resolution and cryptographic audit logging
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.security.audit import record_audit_event
from app.security.consent import is_consent_granted
from app.security.device_identity import DeviceIdentity, get_device_identity
from app.security.device_store import get_trusted_device
from app.security.envelope import CryptographicEnvelope, open_payload, seal_payload
from app.sync.conflict import ConflictResolver
from app.sync.models import (
    ClockRelationship,
    SyncBatch,
    SyncChangeType,
    SyncEntityType,
    SyncItem,
    SyncSessionReport,
)
from app.sync.store import (
    get_latest_changes,
    get_sync_state,
    initialize_sync_store,
    record_sync_change,
    record_sync_tombstone,
    update_sync_state,
)
from app.sync.vector_clock import VectorClock
from app.workspace.store import (
    create_workspace,
    get_workspace,
    list_workspaces_for_user,
    update_workspace,
)

try:
    from config import DOCUMENTS_DIR
except ImportError:
    from app.config import DOCUMENTS_DIR

logger = logging.getLogger("lifeos.sync.engine")


class SyncEngine:
    """
    Manages end-to-end synchronization sessions with trusted peer devices.
    """

    def __init__(
        self,
        local_identity: Optional[DeviceIdentity] = None,
        conflict_resolver: Optional[ConflictResolver] = None,
        db_path: Path | str | None = None,
    ):
        self.local_identity = local_identity or get_device_identity()
        self.conflict_resolver = conflict_resolver or ConflictResolver(
            local_device_id=self.local_identity.device_id
        )
        self.db_path = db_path
        initialize_sync_store(self.db_path)

    def export_sync_package(
        self,
        peer_device_id: str,
        workspace_id: Optional[str] = None,
        user_id: str = "system",
    ) -> CryptographicEnvelope:
        """
        Assemble, seal, and digitally sign a sync package for a trusted peer device.
        Enforces workspace air-gaps, consent grants, and peer trust state.
        """
        # 1. Peer trust verification
        peer = get_trusted_device(peer_device_id)
        if not peer:
            raise ValueError(f"Peer device '{peer_device_id}' is not in the trusted device store.")
        if peer.get("trust_status") != "paired":
            raise PermissionError(
                f"Cannot synchronize with peer '{peer_device_id}' in state '{peer.get('trust_status')}'."
            )

        # 2. Workspace boundary & local_only check
        allowed_workspaces: List[str] = []
        if workspace_id:
            ws = get_workspace(workspace_id)
            if not ws:
                raise ValueError(f"Workspace '{workspace_id}' not found.")
            if ws.is_local_only:
                raise PermissionError(
                    f"Workspace '{workspace_id}' is configured as local_only; cross-device sync is forbidden."
                )
            allowed_workspaces.append(workspace_id)
        else:
            all_ws = list_workspaces_for_user(user_id=user_id, is_admin=True)
            for ws in all_ws:
                if not ws.is_local_only:
                    allowed_workspaces.append(ws.workspace_id)

        # 3. Collect sync items across allowed workspaces
        sync_items: List[SyncItem] = []
        for ws_id in allowed_workspaces:
            # Query sync change ledger
            changes = get_latest_changes(workspace_id=ws_id, db_path=self.db_path)
            sync_items.extend(changes)

            # Context items in context.db
            try:
                from app.context.context_store import get_all_context
                ctx_map = get_all_context(workspace_id=ws_id)
                for k, v in ctx_map.items():
                    # Only add if not already present in sync_items
                    if not any(i.entity_type == SyncEntityType.CONTEXT and i.entity_id == k and i.workspace_id == ws_id for i in sync_items):
                        clock = VectorClock().increment(self.local_identity.device_id, 1)
                        item = record_sync_change(
                            entity_type=SyncEntityType.CONTEXT,
                            entity_id=k,
                            data={"key": k, "value": v, "updated_at": datetime.now(timezone.utc).isoformat()},
                            workspace_id=ws_id,
                            origin_device_id=self.local_identity.device_id,
                            vector_clock=clock,
                            db_path=self.db_path,
                        )
                        sync_items.append(item)
            except Exception as e:
                logger.warning(f"Error gathering context items for sync: {e}")

            # Workspace metadata itself
            ws_profile = get_workspace(ws_id)
            if ws_profile and not any(i.entity_type == SyncEntityType.WORKSPACE and i.entity_id == ws_id for i in sync_items):
                ws_clock = VectorClock().increment(self.local_identity.device_id, 1)
                ws_item = record_sync_change(
                    entity_type=SyncEntityType.WORKSPACE,
                    entity_id=ws_id,
                    data=ws_profile.to_dict(),
                    workspace_id=ws_id,
                    origin_device_id=self.local_identity.device_id,
                    vector_clock=ws_clock,
                    db_path=self.db_path,
                )
                sync_items.append(ws_item)

        # 4. Construct manifest
        manifest: Dict[str, int] = {}
        for item in sync_items:
            t = item.entity_type.value
            manifest[t] = manifest.get(t, 0) + 1

        batch = SyncBatch(
            batch_id=f"bat_{uuid.uuid4().hex[:12]}",
            sender_device_id=self.local_identity.device_id,
            recipient_device_id=peer_device_id,
            items=sync_items,
            timestamp=datetime.now(timezone.utc).isoformat(),
            manifest=manifest,
        )

        # 5. Seal in CryptographicEnvelope (E2EE X25519-HKDF-AES256GCM + Ed25519 Signature)
        envelope = seal_payload(
            payload=batch.model_dump_json().encode("utf-8"),
            recipient_exchange_public_key_b64=peer["exchange_public_key"],
            recipient_device_id=peer_device_id,
            sender_identity=self.local_identity,
        )

        # 6. Cryptographic audit log
        record_audit_event(
            event_type="sync_package_exported",
            actor=user_id or "system",
            resource=peer_device_id,
            purpose="cross_device_sync",
            status="success",
            details={
                "batch_id": batch.batch_id,
                "items_count": len(sync_items),
                "manifest": manifest,
                "workspaces": allowed_workspaces,
            },
        )

        return envelope

    def import_sync_package(
        self,
        envelope_data: CryptographicEnvelope | Dict[str, Any],
        user_id: str = "system",
    ) -> SyncSessionReport:
        """
        Decrypt, verify, and ingest a cryptographic sync envelope received from a peer.
        Enforces peer trust, vector clock ordering, and non-destructive conflict handling.
        """
        env = envelope_data if isinstance(envelope_data, CryptographicEnvelope) else CryptographicEnvelope.from_dict(envelope_data)
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Peer trust verification
        peer = get_trusted_device(env.sender_device_id)
        if not peer:
            raise PermissionError(f"Sender device '{env.sender_device_id}' is unknown and not trusted.")
        if peer.get("trust_status") != "paired":
            raise PermissionError(f"Cannot accept sync from peer with status '{peer.get('trust_status')}'.")

        # 2. Cryptographic decryption and signature verification
        plaintext_bytes = open_payload(
            envelope=env,
            recipient_identity=self.local_identity,
            sender_signing_public_key_b64=peer["signing_public_key"],
        )

        batch_dict = json.loads(plaintext_bytes.decode("utf-8"))
        batch = SyncBatch.model_validate(batch_dict)

        if batch.recipient_device_id != self.local_identity.device_id:
            raise ValueError(f"Sync envelope recipient '{batch.recipient_device_id}' does not match this device.")

        # 3. Process items in batch
        items_imported = 0
        items_skipped = 0
        conflicts_detected = 0
        conflicts_resolved = 0

        # Import helper subroutines
        from app.context.context_store import delete_context, get_context, set_context

        for item in batch.items:
            # Check local workspace isolation
            local_ws = get_workspace(item.workspace_id)
            if local_ws and local_ws.is_local_only:
                logger.warning(f"Skipping sync item for local_only workspace '{item.workspace_id}'")
                items_skipped += 1
                continue

            # Process by entity type
            if item.entity_type == SyncEntityType.CONTEXT:
                existing_val = get_context(item.entity_id, workspace_id=item.workspace_id)
                remote_clock = VectorClock.from_dict(item.vector_clock)

                if existing_val is None:
                    # Item does not exist locally
                    if item.is_tombstone:
                        record_sync_tombstone(
                            entity_type=SyncEntityType.CONTEXT,
                            entity_id=item.entity_id,
                            workspace_id=item.workspace_id,
                            origin_device_id=item.origin_device_id,
                            vector_clock=remote_clock,
                            db_path=self.db_path,
                        )
                    else:
                        set_context(
                            key=item.entity_id,
                            value=item.data.get("value", ""),
                            context_type=item.data.get("context_type", "general"),
                            workspace_id=item.workspace_id,
                        )
                        record_sync_change(
                            entity_type=SyncEntityType.CONTEXT,
                            entity_id=item.entity_id,
                            data=item.data,
                            workspace_id=item.workspace_id,
                            origin_device_id=item.origin_device_id,
                            vector_clock=remote_clock,
                            db_path=self.db_path,
                        )
                    items_imported += 1
                else:
                    # Item exists locally: fetch local clock
                    local_change = get_latest_changes(workspace_id=item.workspace_id, db_path=self.db_path)
                    local_match = next((c for c in local_change if c.entity_type == SyncEntityType.CONTEXT and c.entity_id == item.entity_id), None)
                    local_clock = VectorClock.from_dict(local_match.vector_clock if local_match else {})

                    relation = local_clock.compare(remote_clock)

                    if relation == ClockRelationship.ANCESTOR:
                        # Remote is strictly newer!
                        if item.is_tombstone:
                            delete_context(item.entity_id, workspace_id=item.workspace_id)
                            record_sync_tombstone(
                                entity_type=SyncEntityType.CONTEXT,
                                entity_id=item.entity_id,
                                workspace_id=item.workspace_id,
                                origin_device_id=item.origin_device_id,
                                vector_clock=remote_clock,
                                db_path=self.db_path,
                            )
                        else:
                            set_context(
                                key=item.entity_id,
                                value=item.data.get("value", ""),
                                context_type=item.data.get("context_type", "general"),
                                workspace_id=item.workspace_id,
                            )
                            record_sync_change(
                                entity_type=SyncEntityType.CONTEXT,
                                entity_id=item.entity_id,
                                data=item.data,
                                workspace_id=item.workspace_id,
                                origin_device_id=item.origin_device_id,
                                vector_clock=remote_clock,
                                db_path=self.db_path,
                            )
                        items_imported += 1

                    elif relation == ClockRelationship.DESCENDANT:
                        # Local is strictly newer!
                        items_skipped += 1

                    elif relation == ClockRelationship.EQUAL:
                        # Already identical
                        items_skipped += 1

                    elif relation == ClockRelationship.CONCURRENT:
                        # Concurrent divergence: invoke ConflictResolver
                        conflicts_detected += 1
                        local_data = local_match.data if local_match else {"value": existing_val, "updated_at": ""}
                        winning_data, conflict = self.conflict_resolver.resolve_context_conflict(
                            entity_id=item.entity_id,
                            workspace_id=item.workspace_id,
                            peer_device_id=env.sender_device_id,
                            local_data=local_data,
                            remote_data=item.data,
                            local_clock=local_clock.to_dict(),
                            remote_clock=remote_clock.to_dict(),
                            db_path=self.db_path,
                        )
                        # Apply winner
                        set_context(
                            key=item.entity_id,
                            value=winning_data.get("value", ""),
                            context_type=winning_data.get("context_type", "general"),
                            workspace_id=item.workspace_id,
                        )
                        # Record merged clock
                        merged_clock = local_clock.merge(remote_clock)
                        record_sync_change(
                            entity_type=SyncEntityType.CONTEXT,
                            entity_id=item.entity_id,
                            data=winning_data,
                            workspace_id=item.workspace_id,
                            origin_device_id=self.local_identity.device_id,
                            vector_clock=merged_clock,
                            db_path=self.db_path,
                        )
                        conflicts_resolved += 1
                        items_imported += 1

            elif item.entity_type == SyncEntityType.WORKSPACE:
                # Workspace metadata sync
                target_ws = get_workspace(item.entity_id)
                if not target_ws:
                    create_workspace(
                        name=item.data.get("name", "Synced Workspace"),
                        profile_type=item.data.get("profile_type", "personal"),
                        description=item.data.get("description"),
                        settings=item.data.get("settings", {}),
                        workspace_id=item.entity_id,
                    )
                    items_imported += 1
                else:
                    # Update local workspace
                    update_workspace(
                        workspace_id=item.entity_id,
                        name=item.data.get("name"),
                        description=item.data.get("description"),
                        settings=item.data.get("settings"),
                    )
                    items_imported += 1

            elif item.entity_type == SyncEntityType.DOCUMENT_META:
                # Document metadata & non-destructive conflict handling
                remote_hash = item.data.get("file_hash", "")
                remote_rel_path = item.entity_id

                from app.knowledge.document_registry import search_documents
                local_docs = search_documents(query="", workspace_id=item.workspace_id)
                local_doc = next((d for d in local_docs if d.get("filename") == Path(remote_rel_path).name), None)

                if local_doc and local_doc.get("file_hash") != remote_hash and remote_hash != "":
                    # Concurrent document conflict!
                    conflicts_detected += 1
                    forked_path, forked_data, conflict = self.conflict_resolver.resolve_document_conflict(
                        entity_id=remote_rel_path,
                        workspace_id=item.workspace_id,
                        peer_device_id=env.sender_device_id,
                        local_data=local_doc,
                        remote_data=item.data,
                        local_clock={},
                        remote_clock=item.vector_clock,
                        db_path=self.db_path,
                    )
                    conflicts_resolved += 1
                    items_imported += 1
                else:
                    items_imported += 1

        # 4. Update sync state for peer
        update_sync_state(
            peer_device_id=env.sender_device_id,
            workspace_id="*",
            peer_vector_clock=VectorClock(),
            local_sequence=len(batch.items),
            db_path=self.db_path,
        )

        # 5. Audit event
        record_audit_event(
            event_type="sync_package_imported",
            actor=user_id or "system",
            resource=env.sender_device_id,
            purpose="cross_device_sync",
            status="success",
            details={
                "batch_id": batch.batch_id,
                "items_imported": items_imported,
                "items_skipped": items_skipped,
                "conflicts_detected": conflicts_detected,
                "conflicts_resolved": conflicts_resolved,
            },
        )

        # 6. Publish LifeOS events
        try:
            from app.events.bus import get_event_bus
            from app.events.models import LifeOSEvent
            get_event_bus().publish_sync(
                LifeOSEvent(
                    event_type="sync.completed",
                    producer="sync",
                    payload={
                        "peer_device_id": env.sender_device_id,
                        "batch_id": batch.batch_id,
                        "items_imported": items_imported,
                        "conflicts_resolved": conflicts_resolved,
                    },
                )
            )
        except Exception:
            pass

        return SyncSessionReport(
            session_id=batch.batch_id,
            peer_device_id=env.sender_device_id,
            items_exported=0,
            items_imported=items_imported,
            items_skipped=items_skipped,
            conflicts_detected=conflicts_detected,
            conflicts_resolved=conflicts_resolved,
            timestamp=now_iso,
            success=True,
        )


# Global Engine Singleton & Factory
_sync_engine_instance: Optional[SyncEngine] = None


def get_sync_engine() -> SyncEngine:
    global _sync_engine_instance
    if _sync_engine_instance is None:
        _sync_engine_instance = SyncEngine()
    return _sync_engine_instance


def reset_sync_engine() -> None:
    global _sync_engine_instance
    _sync_engine_instance = None
