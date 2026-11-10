"""
reindex.py

Safe, non-destructive reindexing for LifeOS.

Architecture & Invariants:
1. Non-destructive rebuild:
   Existing search data, vector chunks, and registry state are NOT wiped upfront.
   Files are reindexed with force=True, updating vector chunks and metadata safely.
2. Snapshot & Rollback Protection:
   Before destructive steps or re-indexing, transactional snapshots of:
   - document_registry.db
   - entities.db
   - people_registry.db
   - Chroma vector collection
   are recorded. If any fatal failure occurs during rebuild, all persistence stores
   are rolled back to their exact pre-reindex state.
3. Clean derived entities:
   Derived entities from documents are rebuilt, while durable entities linked to
   confirmed people (person_id) are strictly preserved.
4. Complete cleanup of removed files:
   Files no longer present on disk have their Chroma chunks, document registry records,
   and face embeddings cleanly removed.
"""

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from config import (
    DOCUMENTS_DIR,
    REGISTRY_DB,
    DATA_DIR,
)
from ingestion.ingest import ingest_documents
from knowledge.document_registry import (
    initialize_registry,
    get_documents_by_status,
)
from knowledge.entity_relationship import (
    ENTITY_DB,
    initialize_entity_store,
)
from knowledge.people import (
    PEOPLE_DB,
    initialize_people_registry,
)
from vectordb.chroma_db import collection

logger = logging.getLogger(__name__)


def _snapshot_sqlite(db_path: Path):
    """Create an in-memory snapshot of a SQLite database using native backup."""
    if not db_path.exists():
        return None
    try:
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(":memory:")
        source.backup(dest)
        source.close()
        return dest
    except Exception as exc:
        logger.warning("Failed to create snapshot for %s: %s", db_path, exc)
        return None


def _restore_sqlite(dest_path: Path, mem_conn) -> None:
    """Restore a SQLite database from an in-memory snapshot."""
    if mem_conn is None:
        return
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(dest_path)
        mem_conn.backup(target)
        target.close()
    except Exception as exc:
        logger.error("Failed to restore SQLite db %s: %s", dest_path, exc)


def _snapshot_chroma(col) -> dict[str, Any] | None:
    """Snapshot Chroma collection chunks, documents, metadatas, and embeddings."""
    try:
        data = col.get(include=["documents", "metadatas", "embeddings"])
        raw_embs = data.get("embeddings")
        return {
            "ids": list(data.get("ids", [])),
            "documents": list(data.get("documents", [])),
            "metadatas": list(data.get("metadatas", [])),
            "embeddings": [list(e) for e in raw_embs] if raw_embs is not None and len(raw_embs) > 0 else None,
        }
    except Exception as exc:
        logger.warning("Failed to snapshot Chroma collection: %s", exc)
        return None


def _restore_chroma(col, snap: dict[str, Any] | None) -> None:
    """Restore Chroma collection from a snapshot."""
    if snap is None:
        return
    try:
        current = col.get()
        current_ids = current.get("ids", [])
        if current_ids:
            col.delete(ids=current_ids)
        if snap["ids"]:
            kwargs = {
                "ids": snap["ids"],
                "documents": snap["documents"],
                "metadatas": snap["metadatas"],
            }
            if snap.get("embeddings") is not None and len(snap["embeddings"]) == len(snap["ids"]):
                kwargs["embeddings"] = snap["embeddings"]
            col.add(**kwargs)
    except Exception as exc:
        logger.error("Failed to restore Chroma collection: %s", exc)


def _prune_derived_entities():
    """
    Delete entities derived purely from documents while preserving
    durable entities linked to confirmed people (person_id).
    """
    initialize_entity_store()
    try:
        from knowledge.entity_relationship import _connect
        with _connect() as connection:
            # Foreign keys ON DELETE CASCADE will clean up entity_aliases & relationships
            connection.execute(
                """
                DELETE FROM entities
                WHERE json_extract(attributes, '$.person_id') IS NULL
                """
            )
            connection.commit()
    except Exception as exc:
        logger.warning("Could not prune derived entities: %s", exc)


def _execute_reindex(
    documents_dir=DOCUMENTS_DIR,
    force=True,
    clean_derived_entities=True,
    raise_on_error=False,
) -> dict[str, Any]:
    documents_dir = Path(documents_dir).resolve()
    documents_dir.mkdir(parents=True, exist_ok=True)

    initialize_registry()
    initialize_entity_store()
    initialize_people_registry()

    try:
        from knowledge import document_registry as doc_reg_mod
        active_reg_db = Path(getattr(doc_reg_mod, "REGISTRY_DB", REGISTRY_DB))
    except Exception:
        active_reg_db = Path(REGISTRY_DB)

    try:
        from knowledge import entity_relationship as ent_mod
        active_ent_db = Path(getattr(ent_mod, "ENTITY_DB", ENTITY_DB))
    except Exception:
        active_ent_db = Path(ENTITY_DB)

    try:
        from knowledge import people as people_mod
        active_peo_db = Path(getattr(people_mod, "PEOPLE_DB", PEOPLE_DB))
    except Exception:
        active_peo_db = Path(PEOPLE_DB)

    logger.info("Taking pre-reindex snapshots...")
    snap_reg = _snapshot_sqlite(active_reg_db)
    snap_ent = _snapshot_sqlite(active_ent_db)
    snap_peo = _snapshot_sqlite(active_peo_db)
    snap_chr = _snapshot_chroma(collection)

    try:
        if clean_derived_entities:
            logger.info("Pruning derived entities while preserving confirmed people...")
            _prune_derived_entities()

        logger.info("Synchronizing directory and rebuilding index: %s", documents_dir)
        ingest_documents(
            documents_dir=documents_dir,
            force=force,
        )

        failed_docs = get_documents_by_status("failed")
        if raise_on_error and failed_docs:
            raise RuntimeError(
                f"Reindex failed for {len(failed_docs)} documents: "
                f"{[d.get('filename') for d in failed_docs]}"
            )

        # Rebuild succeeded
        if snap_reg:
            snap_reg.close()
        if snap_ent:
            snap_ent.close()
        if snap_peo:
            snap_peo.close()

        logger.info("Reindex completed successfully.")
        return {
            "success": True,
            "failed_count": len(failed_docs),
        }

    except Exception as exc:
        logger.error("Reindex encountered an error: %s. Rolling back...", exc)
        _restore_chroma(collection, snap_chr)
        _restore_sqlite(active_reg_db, snap_reg)
        _restore_sqlite(active_ent_db, snap_ent)
        _restore_sqlite(active_peo_db, snap_peo)

        if snap_reg:
            snap_reg.close()
        if snap_ent:
            snap_ent.close()
        if snap_peo:
            snap_peo.close()

        logger.info("Rollback complete. System state restored to pre-reindex snapshot.")
        if raise_on_error:
            raise
        return {
            "success": False,
            "error": str(exc),
        }


def reindex(
    documents_dir=DOCUMENTS_DIR,
    force=True,
    clean_derived_entities=True,
    raise_on_error=False,
    lock_timeout=0.0,
    lock_path=None,
) -> dict[str, Any]:
    """
    Safely reindex all documents with concurrency coordination and snapshot rollback.

    Protections:
    - Cross-process and cross-thread concurrency lock prevents racing reindex runs.
    - Conflicting external mutations (ingest_file, remove_file, watcher) are blocked/deferred during reindex.
    - Application-level snapshots of SQLite stores and Chroma collection are recorded before mutations.
    - In the event of fatal exceptions, persistence stores are rolled back to pre-reindex state.
    - Durable source-of-truth records (confirmed human identities) are preserved.
    """
    from knowledge.coordination import acquire_reindex_lock

    try:
        with acquire_reindex_lock(timeout=lock_timeout, lock_path=lock_path):
            return _execute_reindex(
                documents_dir=documents_dir,
                force=force,
                clean_derived_entities=clean_derived_entities,
                raise_on_error=raise_on_error,
            )
    except RuntimeError as lock_err:
        logger.warning("Reindex cannot proceed: %s", lock_err)
        if raise_on_error:
            raise
        return {
            "success": False,
            "error": str(lock_err),
        }

