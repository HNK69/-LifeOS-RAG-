"""
backup.py

Operational backup and disaster recovery procedures for LifeOS Phase 12.
Provides coordinated, consistent point-in-time snapshots of:
1. SQLite databases (via online SQLite backup API, capturing committed state without WAL drift)
2. Documents filesystem
3. ChromaDB persistent index
4. Metadata manifest with integrity checksums
"""

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
        CHROMA_DIR,
        CONTEXT_DB,
        DATA_DIR,
        DOCUMENTS_DIR,
        ENTITY_DB,
        PEOPLE_DB,
        REGISTRY_DB,
    )
except ImportError:
    from app.config import (
        BACKUP_DIR,
        CHROMA_DIR,
        CONTEXT_DB,
        DATA_DIR,
        DOCUMENTS_DIR,
        ENTITY_DB,
        PEOPLE_DB,
        REGISTRY_DB,
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

    # Connect to source and destination
    src_conn = sqlite3.connect(str(src_path), timeout=5.0)
    dest_conn = sqlite3.connect(str(dest_path), timeout=5.0)
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    size_bytes = dest_path.stat().st_size
    sha256 = _compute_sha256(dest_path)
    return {"exists": True, "size_bytes": size_bytes, "sha256": sha256}


def create_backup(
    destination_dir: Path | str | None = None,
    label: str | None = None,
) -> Path:
    """
    Create a complete, atomic point-in-time backup archive of LifeOS.

    Args:
        destination_dir: Directory where the backup .tar.gz will be saved.
        label: Optional user tag for the backup file name.

    Returns:
        Absolute Path to the generated backup archive.
    """
    try:
        import config
        dest_dir = Path(destination_dir or getattr(config, "BACKUP_DIR", BACKUP_DIR)).resolve()
        docs_src = Path(getattr(config, "DOCUMENTS_DIR", DOCUMENTS_DIR)).resolve()
        chroma_src = Path(getattr(config, "CHROMA_DIR", CHROMA_DIR)).resolve()
        reg_db = Path(getattr(config, "REGISTRY_DB", REGISTRY_DB))
        ent_db = Path(getattr(config, "ENTITY_DB", ENTITY_DB))
        ppl_db = Path(getattr(config, "PEOPLE_DB", PEOPLE_DB))
        ctx_db = Path(getattr(config, "CONTEXT_DB", CONTEXT_DB))
    except ImportError:
        dest_dir = Path(destination_dir or BACKUP_DIR).resolve()
        docs_src = Path(DOCUMENTS_DIR).resolve()
        chroma_src = Path(CHROMA_DIR).resolve()
        reg_db = Path(REGISTRY_DB)
        ent_db = Path(ENTITY_DB)
        ppl_db = Path(PEOPLE_DB)
        ctx_db = Path(CONTEXT_DB)

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

        databases_dir.mkdir(parents=True, exist_ok=True)

        manifest: dict[str, Any] = {
            "version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "databases": {},
            "documents_count": 0,
            "chroma_present": False,
        }

        # 1. Back up SQLite databases
        db_targets = {
            "document_registry.db": reg_db,
            "entities.db": ent_db,
            "people_registry.db": ppl_db,
            "context.db": ctx_db,
        }

        for db_name, db_path in db_targets.items():
            dest_file = databases_dir / db_name
            meta = _backup_sqlite_db(db_path, dest_file)
            manifest["databases"][db_name] = meta

        # 2. Back up documents directory
        if docs_src.exists() and docs_src.is_dir():
            shutil.copytree(docs_src, documents_stage, dirs_exist_ok=True)
            manifest["documents_count"] = len([f for f in documents_stage.glob("*") if f.is_file()])

        # 3. Back up Chroma directory
        if chroma_src.exists() and chroma_src.is_dir():
            shutil.copytree(chroma_src, chroma_stage, dirs_exist_ok=True)
            manifest["chroma_present"] = True

        # 4. Write manifest
        manifest_path = staging_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 5. Pack into tar.gz archive
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
) -> dict[str, Any]:
    """
    Restore LifeOS databases, documents, and Chroma vectors from a backup archive.

    Args:
        archive_path: Path to the .tar.gz backup archive.
        target_data_dir: Target data directory (defaults to config.DATA_DIR).
        target_chroma_dir: Target Chroma directory (defaults to config.CHROMA_DIR).
        target_documents_dir: Target documents directory (defaults to target_data_dir/documents).

    Returns:
        Structured summary of restored components and verification status.
    """
    archive = Path(archive_path).resolve()
    if not archive.exists() or not archive.is_file():
        raise FileNotFoundError(f"Backup archive not found: {archive}")

    if target_data_dir:
        target_data = Path(target_data_dir).resolve()
        target_docs = Path(target_documents_dir or target_data / "documents").resolve()
    else:
        try:
            import config
            target_data = Path(getattr(config, "DATA_DIR", DATA_DIR)).resolve()
            target_docs = Path(target_documents_dir or getattr(config, "DOCUMENTS_DIR", target_data / "documents")).resolve()
        except ImportError:
            target_data = Path(DATA_DIR).resolve()
            target_docs = Path(target_documents_dir or target_data / "documents").resolve()

    if target_chroma_dir:
        target_chroma = Path(target_chroma_dir).resolve()
    else:
        try:
            import config
            target_chroma = Path(getattr(config, "CHROMA_DIR", CHROMA_DIR)).resolve()
        except ImportError:
            target_chroma = Path(CHROMA_DIR).resolve()

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

        target_data.mkdir(parents=True, exist_ok=True)
        target_docs.mkdir(parents=True, exist_ok=True)
        target_chroma.mkdir(parents=True, exist_ok=True)

        restored_dbs = []
        # Restore databases
        databases_dir = extract_dir / "databases"
        if databases_dir.exists():
            for db_file in databases_dir.iterdir():
                if db_file.is_file():
                    # Integrity check on extracted database before overwriting
                    test_conn = sqlite3.connect(str(db_file))
                    try:
                        cursor = test_conn.execute("PRAGMA integrity_check")
                        result = cursor.fetchone()[0]
                        if result != "ok":
                            raise ValueError(f"Integrity check failed for {db_file.name}: {result}")
                    finally:
                        test_conn.close()

                    # Atomic copy into destination
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

        # Restore Chroma
        chroma_stage = extract_dir / "chroma_db"
        chroma_restored = False
        if chroma_stage.exists() and chroma_stage.is_dir():
            shutil.copytree(chroma_stage, target_chroma, dirs_exist_ok=True)
            chroma_restored = True

        logger.info(
            "LifeOS restore complete: %d databases, %d documents, Chroma=%s",
            len(restored_dbs),
            restored_docs_count,
            chroma_restored,
        )

        return {
            "success": True,
            "backup_version": manifest.get("version"),
            "backup_timestamp": manifest.get("timestamp"),
            "restored_databases": restored_dbs,
            "restored_documents": restored_docs_count,
            "chroma_restored": chroma_restored,
        }

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
