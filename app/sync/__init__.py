"""
LifeOS Cross-Device Synchronization & Conflict Resolution Package (Milestone 8).
"""

from app.sync.models import (
    ClockRelationship,
    SyncBatch,
    SyncChangeType,
    SyncConflict,
    SyncEntityType,
    SyncItem,
    SyncSessionReport,
)
from app.sync.vector_clock import VectorClock
from app.sync.store import (
    initialize_sync_store,
    record_sync_change,
    record_sync_tombstone,
    get_sync_state,
    update_sync_state,
    record_sync_conflict,
    resolve_sync_conflict,
    list_sync_conflicts,
)
from app.sync.conflict import ConflictResolver
from app.sync.engine import SyncEngine, get_sync_engine

__all__ = [
    "ClockRelationship",
    "SyncBatch",
    "SyncChangeType",
    "SyncConflict",
    "SyncEntityType",
    "SyncItem",
    "SyncSessionReport",
    "VectorClock",
    "initialize_sync_store",
    "record_sync_change",
    "record_sync_tombstone",
    "get_sync_state",
    "update_sync_state",
    "record_sync_conflict",
    "resolve_sync_conflict",
    "list_sync_conflicts",
    "ConflictResolver",
    "SyncEngine",
    "get_sync_engine",
]
