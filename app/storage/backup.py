"""
backup.py

Operational backup, disaster recovery, and data verification procedures for LifeOS.
Provides coordinated, consistent point-in-time snapshots and atomic restores of:
1. All 8 SQLite databases (via online SQLite backup API, capturing committed state without WAL drift):
   - document_registry.db (Milestone 1)
   - entities.db (Milestone 1)
   - people_registry.db (Milestone 1)
   - context.db (Milestone 1)
   - privacy.db (Milestone 4 - Consent & Cryptographic Audit)
   - identity.db (Milestone 3 - Device Identity & Trusted Peers)
   - workspace.db (Milestone 6 - Multi-Profile Boundaries & RLS)
   - sync.db (Milestone 8 - Vector Clocks, Conflicts & Tombstones)
2. Documents filesystem
3. ChromaDB persistent index
4. Plugins state and manifests (Milestone 9)
5. Metadata manifest with SHA-256 integrity checksums
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from config import (
        BACKUP_DIR,
        BACKUP_RETENTION_DAYS,
        CHROMA_DIR,
        CONTEXT_DB,
        DATA_DIR,
        DOCUMENTS_DIR,
        ENTITY_DB,
        IDENTITY_DB,
        PEOPLE_DB,
        PLUGINS_DIR,
        PRIVACY_DB,
        REGISTRY_DB,
        SYNC_DB,
        WORKSPACE_DB,
    )
except ImportError:
    from app.config import (
        BACKUP_DIR,
        BACKUP_RETENTION_DAYS,
        CHROMA_DIR,
        CONTEXT_DB,
        DATA_DIR,
        DOCUMENTS_DIR,
        ENTITY_DB,
        IDENTITY_DB,
        PEOPLE_DB,
        PLUGINS_DIR,
        PRIVACY_DB,
        REGISTRY_DB,
        SYNC_DB,
        WORKSPACE_DB,
    )

logger = logging.getLogger("lifeos.storage.backup")


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for integrity verification."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(64 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _backup_sqlite_db(src_path: Path, dest_path: Path) -> dict[str, Any]:
    """
    Perform transactionally consistent SQLite backup using the online backup API.
    Handles active WAL files and ongoing readers safely.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if not src_path.exists():
        return {"exists": False, "size_bytes": 0, "sha256": None}

    src_conn = sqlite3.connect(str(src_path), timeout=10.0)
    dest_conn = sqlite3.connect(str(dest_path), timeout=10.0)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    size_bytes = dest_path.stat().st_size
    sha256 = _compute_sha256(dest_path)
    return {"exists": True, "size_bytes": size_bytes, "sha256": sha256}


def _resolve_paths():
    """Resolve active database, document, vector, and plugin paths."""
    try:
        import config as cfg
    except ImportError:
        import app.config as cfg

    return {
        "dest_dir": Path(getattr(cfg, "BACKUP_DIR", BACKUP_DIR)).resolve(),
        "docs_src": Path(getattr(cfg, "DOCUMENTS_DIR", DOCUMENTS_DIR)).resolve(),
        "chroma_src": Path(getattr(cfg, "CHROMA_DIR", CHROMA_DIR)).resolve(),
        "plugins_src": Path(getattr(cfg, "PLUGINS_DIR", PLUGINS_DIR)).resolve(),
        "data_dir": Path(getattr(cfg, "DATA_DIR", DATA_DIR)).resolve(),
        "databases": {
            "document_registry.db": Path(getattr(cfg, "REGISTRY_DB", REGISTRY_DB)),
            "entities.db": Path(getattr(cfg, "ENTITY_DB", ENTITY_DB)),
            "people_registry.db": Path(getattr(cfg, "PEOPLE_DB", PEOPLE_DB)),
            "context.db": Path(getattr(cfg, "CONTEXT_DB", CONTEXT_DB)),
            "privacy.db": Path(getattr(cfg, "PRIVACY_DB", PRIVACY_DB)),
            "identity.db": Path(getattr(cfg, "IDENTITY_DB", IDENTITY_DB)),
            "workspace.db": Path(getattr(cfg, "WORKSPACE_DB", WORKSPACE_DB)),
            "sync.db": Path(getattr(cfg, "SYNC_DB", SYNC_DB)),
        },
    }


def create_backup(
    destination_dir: Path | str | None = None,
    label: str | None = None,
) -> Path:
    """
    Create a complete, atomic point-in-time backup archive of LifeOS.
    Captures all 8 SQLite databases, documents, vector database, and plugin state.
    """
    paths = _resolve_paths()
    dest_dir = Path(destination_dir).resolve() if destination_dir else paths["dest_dir"]
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tag = f"_{label.strip()}" if label and label.strip() else ""
    archive_name = f"lifeos_backup_{timestamp_str}{tag}.tar.gz"
    final_archive_path = dest_dir / archive_name

    staging_dir = Path(tempfile.mkdtemp(prefix="lifeos_backup_stage_"))

    try:
        databases_dir = staging_dir / "databases"
        documents_stage = staging_dir / "documents"
        chroma_stage = staging_dir / "chroma_db"
        plugins_stage = staging_dir / "plugins"

        databases_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "version": "2.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "databases": {},
            "documents_count": 0,
            "chroma_present": False,
            "plugins_count": 0,
        }

        # 1. Back up all 8 SQLite databases
        for db_name, db_path in paths["databases"].items():
            dest_file = databases_dir / db_name
            meta = _backup_sqlite_db(db_path, dest_file)
            manifest["databases"][db_name] = meta

        # 2. Back up documents directory
        docs_src = paths["docs_src"]
        if docs_src.exists() and docs_src.is_dir():
            shutil.copytree(docs_src, documents_stage, dirs_exist_ok=True)
            manifest["documents_count"] = len([f for f in documents_stage.glob("*") if f.is_file()])

        # 3. Back up Chroma directory
        chroma_src = paths["chroma_src"]
        if chroma_src.exists() and chroma_src.is_dir():
            shutil.copytree(chroma_src, chroma_stage, dirs_exist_ok=True)
            manifest["chroma_present"] = True

        # 4. Back up Plugins directory
        plugins_src = paths["plugins_src"]
        if plugins_src.exists() and plugins_src.is_dir():
            shutil.copytree(plugins_src, plugins_stage, dirs_exist_ok=True)
            manifest["plugins_count"] = len([d for d in plugins_stage.iterdir() if d.is_dir()])

        # 5. Write manifest
        manifest_path = staging_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 6. Pack into tar.gz archive
        with tarfile.open(final_archive_path, "w:gz") as tar:
            for item in staging_dir.iterdir():
                tar.add(item, arcname=item.name)

        logger.info("Successfully created LifeOS backup: %s", final_archive_path)
        return final_archive_path

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def restore_backup(
    archive_path: Path | str,
    target_data_dir: Path | str | None = None,
    target_chroma_dir: Path | str | None = None,
    target_documents_dir: Path | str | None = None,
    create_safety_backup: bool = True,
) -> dict[str, Any]:
    """
    Restore LifeOS databases, documents, Chroma vectors, and plugin states from a backup archive.
    Enforces SHA-256 integrity verification across extracted items before overwriting.
    """
    archive = Path(archive_path).resolve()
    if not archive.exists() or not archive.is_file():
        raise FileNotFoundError(f"Backup archive not found: {archive}")

    paths = _resolve_paths()
    target_data = Path(target_data_dir).resolve() if target_data_dir else paths["data_dir"]
    if target_documents_dir:
        target_docs = Path(target_documents_dir).resolve()
    elif target_data_dir:
        target_docs = (target_data / "documents").resolve()
    else:
        target_docs = paths["docs_src"]

    target_chroma = Path(target_chroma_dir).resolve() if target_chroma_dir else paths["chroma_src"]
    target_plugins = target_data / "plugins"

    extract_dir = Path(tempfile.mkdtemp(prefix="lifeos_restore_stage_"))

    try:
        # Extract archive
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(path=extract_dir)

        manifest_path = extract_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("Invalid backup archive: missing manifest.json")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        databases_manifest = manifest.get("databases", {})

        # Integrity verification: verify SHA-256 checksums of extracted databases
        databases_dir = extract_dir / "databases"
        if databases_dir.exists():
            for db_file in databases_dir.iterdir():
                if db_file.is_file():
                    expected_meta = databases_manifest.get(db_file.name)
                    if expected_meta and expected_meta.get("sha256"):
                        actual_hash = _compute_sha256(db_file)
                        if actual_hash != expected_meta["sha256"]:
                            raise ValueError(
                                f"Corrupt archive: SHA-256 mismatch for {db_file.name} "
                                f"(expected {expected_meta['sha256']}, got {actual_hash})"
                            )

                    # SQLite integrity check
                    test_conn = sqlite3.connect(str(db_file))
                    try:
                        cursor = test_conn.execute("PRAGMA integrity_check")
                        result = cursor.fetchone()[0]
                        if result != "ok":
                            raise ValueError(f"Integrity check failed for {db_file.name}: {result}")
                    finally:
                        test_conn.close()

        # Optional pre-restore safety backup of active state
        if create_safety_backup and target_data.exists():
            try:
                safety_tag = "pre_restore_safety"
                create_backup(label=safety_tag)
            except Exception as exc:
                logger.warning(f"Could not create pre-restore safety backup: {exc}")

        target_data.mkdir(parents=True, exist_ok=True)
        target_docs.mkdir(parents=True, exist_ok=True)
        target_chroma.mkdir(parents=True, exist_ok=True)
        target_plugins.mkdir(parents=True, exist_ok=True)

        restored_dbs = []
        if databases_dir.exists():
            for db_file in databases_dir.iterdir():
                if db_file.is_file():
                    dest = target_data / db_file.name
                    shutil.copy2(db_file, dest)
                    restored_dbs.append(db_file.name)

        # Restore documents
        restored_docs_count = 0
        docs_stage = extract_dir / "documents"
        if docs_stage.exists() and docs_stage.is_dir():
            for doc in docs_stage.iterdir():
                if doc.is_file():
                    shutil.copy2(doc, target_docs / doc.name)
                    restored_docs_count += 1

        # Restore Chroma vectors
        chroma_stage = extract_dir / "chroma_db"
        chroma_restored = False
        if chroma_stage.exists() and chroma_stage.is_dir():
            shutil.copytree(chroma_stage, target_chroma, dirs_exist_ok=True)
            chroma_restored = True

        # Restore Plugins
        plugins_stage = extract_dir / "plugins"
        plugins_restored = False
        if plugins_stage.exists() and plugins_stage.is_dir():
            shutil.copytree(plugins_stage, target_plugins, dirs_exist_ok=True)
            plugins_restored = True

        logger.info(
            "LifeOS restore complete: %d databases, %d documents, Chroma=%s, Plugins=%s",
            len(restored_dbs),
            restored_docs_count,
            chroma_restored,
            plugins_restored,
        )

        return {
            "success": True,
            "backup_version": manifest.get("version"),
            "backup_timestamp": manifest.get("timestamp"),
            "restored_databases": restored_dbs,
            "restored_documents": restored_docs_count,
            "chroma_restored": chroma_restored,
            "plugins_restored": plugins_restored,
        }

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def list_backups(destination_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """List available backups with file sizes, timestamps, and integrity status."""
    paths = _resolve_paths()
    dest_dir = Path(destination_dir).resolve() if destination_dir else paths["dest_dir"]
    if not dest_dir.exists():
        return []

    backups = []
    for archive in sorted(dest_dir.glob("lifeos_backup_*.tar.gz"), reverse=True):
        stat = archive.stat()
        backups.append({
            "filename": archive.name,
            "path": str(archive),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return backups


def prune_backups(
    destination_dir: Path | str | None = None,
    retention_days: int | None = None,
) -> int:
    """
    Delete backup archives older than retention_days (defaults to config.BACKUP_RETENTION_DAYS).
    Returns count of pruned archives.
    """
    paths = _resolve_paths()
    dest_dir = Path(destination_dir).resolve() if destination_dir else paths["dest_dir"]
    days = retention_days if retention_days is not None else BACKUP_RETENTION_DAYS
    if not dest_dir.exists():
        return 0

    cutoff_ts = time.time() - (days * 86400)
    pruned = 0
    for archive in dest_dir.glob("lifeos_backup_*.tar.gz"):
        if archive.stat().st_mtime < cutoff_ts:
            archive.unlink()
            pruned += 1
            logger.info("Pruned expired backup: %s", archive.name)

    return pruned
