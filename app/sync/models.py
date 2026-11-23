"""
models.py

Domain models and typed schemas for Milestone 8: Cross-Device Synchronization
and Distributed Conflict Resolution.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SyncChangeType(str, Enum):
    UPSERT = "upsert"
    DELETE = "delete"


class SyncEntityType(str, Enum):
    WORKSPACE = "workspace"
    DOCUMENT_META = "document_meta"
    DOCUMENT_CHUNK = "document_chunk"
    CONTEXT = "context"
    PEOPLE_ENTITY = "people_entity"


class ClockRelationship(str, Enum):
    EQUAL = "equal"
    ANCESTOR = "ancestor"       # Local is older / strictly preceded remote
    DESCENDANT = "descendant"   # Local is newer / strictly succeeded remote
    CONCURRENT = "concurrent"   # Concurrent / branched divergent edits


class SyncItem(BaseModel):
    item_id: str
    entity_type: SyncEntityType
    entity_id: str              # Logical canonical key (e.g., relative document path or context key)
    workspace_id: str = "default"
    change_type: SyncChangeType = SyncChangeType.UPSERT
    vector_clock: Dict[str, int] = Field(default_factory=dict)
    lamport_clock: int = 1
    origin_device_id: str
    data: Dict[str, Any] = Field(default_factory=dict)
    data_hash: str = ""
    timestamp: str
    is_tombstone: bool = False


class SyncBatch(BaseModel):
    batch_id: str
    sender_device_id: str
    recipient_device_id: str
    items: List[SyncItem] = Field(default_factory=list)
    timestamp: str
    manifest: Dict[str, int] = Field(default_factory=dict)  # entity_type -> count


class SyncConflict(BaseModel):
    conflict_id: str
    entity_type: str
    entity_id: str
    workspace_id: str
    peer_device_id: str
    local_data: Dict[str, Any] = Field(default_factory=dict)
    remote_data: Dict[str, Any] = Field(default_factory=dict)
    local_clock: Dict[str, int] = Field(default_factory=dict)
    remote_clock: Dict[str, int] = Field(default_factory=dict)
    status: str = "unresolved"                              # 'unresolved', 'resolved'
    resolution_strategy: str = "last_write_wins"             # 'last_write_wins', 'branch_fork', 'manual'
    resolved_value: Optional[Dict[str, Any]] = None
    created_at: str
    resolved_at: Optional[str] = None


class SyncSessionReport(BaseModel):
    session_id: str
    peer_device_id: str
    items_exported: int = 0
    items_imported: int = 0
    items_skipped: int = 0
    conflicts_detected: int = 0
    conflicts_resolved: int = 0
    timestamp: str
    success: bool = True
    error: Optional[str] = None
