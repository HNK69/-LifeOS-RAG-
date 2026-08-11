"""
document_registry.py

Persistent registry for LifeOS documents.

Uses cheap filesystem metadata (size + modification time) for normal
change detection. SHA-256 is calculated only when a file is new or
its metadata indicates that it may have changed.
"""

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

from config import REGISTRY_DB


def _get_connection():
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(REGISTRY_DB)
    connection.row_factory = sqlite3.Row

    return connection


def initialize_registry():
    """Create or migrate the document registry."""

    with _get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                extension TEXT NOT NULL,
                modified_time REAL NOT NULL,
                file_hash TEXT NOT NULL,
                ingestion_status TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                last_ingested_at TEXT,
                error_message TEXT,
                metadata_json TEXT,
                file_size INTEGER DEFAULT 0,
                modified_time_ns INTEGER DEFAULT 0
            )
            """
        )

        columns = {
            column["name"]
            for column in connection.execute(
                "PRAGMA table_info(documents)"
            ).fetchall()
        }

        migrations = {
            "metadata_json": "ALTER TABLE documents ADD COLUMN metadata_json TEXT",
            "file_size": "ALTER TABLE documents ADD COLUMN file_size INTEGER DEFAULT 0",
            "modified_time_ns": (
                "ALTER TABLE documents "
                "ADD COLUMN modified_time_ns INTEGER DEFAULT 0"
            ),
        }

        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(statement)

        connection.commit()


def get_file_signature(file_path):
    """
    Return cheap filesystem metadata.

    Does not read file contents.
    """

    path = Path(file_path).resolve()
    stat = path.stat()

    return {
        "path": str(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "file_size": stat.st_size,
        "modified_time": stat.st_mtime,
        "modified_time_ns": stat.st_mtime_ns,
    }


def calculate_file_hash(file_path):
    """
    Calculate SHA-256 using streaming reads.

    This should only be called when a file is new or its filesystem
    metadata indicates that it may have changed.
    """

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)

            if not chunk:
                break

            sha256.update(chunk)

    return sha256.hexdigest()


def get_document(file_path):
    """Return one registry record."""

    file_path = str(Path(file_path).resolve())

    initialize_registry()

    with _get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE path = ?
            """,
            (file_path,),
        ).fetchone()

    return dict(row) if row is not None else None


def get_all_documents():
    """Return all registered documents."""

    initialize_registry()

    with _get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM documents
            ORDER BY filename
            """
        ).fetchall()

    return [dict(row) for row in rows]


def file_needs_processing(file_path):
    """
    Cheap change detection.

    Returns True when:
    - the file is not registered;
    - the previous ingestion failed;
    - file size changed;
    - modification timestamp changed.

    No file contents are read.
    """

    initialize_registry()

    signature = get_file_signature(file_path)
    record = get_document(file_path)

    if record is None:
        return True

    if record["ingestion_status"] != "success":
        return True

    registered_size = record.get("file_size", 0)
    registered_mtime_ns = record.get("modified_time_ns", 0)

    return not (
        registered_size == signature["file_size"]
        and registered_mtime_ns == signature["modified_time_ns"]
    )


def is_unchanged(file_path, file_hash=None):
    """
    Compatibility helper.

    When file_hash is supplied, compare content hashes.

    When it is not supplied, use cheap filesystem metadata.
    """

    record = get_document(file_path)

    if record is None:
        return False

    if record["ingestion_status"] != "success":
        return False

    if file_hash is not None:
        return record["file_hash"] == file_hash

    return not file_needs_processing(file_path)


def mark_processing(file_path, file_hash):
    """Mark a file as currently being processed."""

    path = Path(file_path).resolve()
    signature = get_file_signature(path)

    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                path,
                filename,
                extension,
                modified_time,
                file_hash,
                ingestion_status,
                chunk_count,
                last_ingested_at,
                error_message,
                metadata_json,
                file_size,
                modified_time_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                error_message = NULL,
                file_size = excluded.file_size,
                modified_time_ns = excluded.modified_time_ns
            """,
            (
                signature["path"],
                signature["filename"],
                signature["extension"],
                signature["modified_time"],
                file_hash,
                "processing",
                0,
                None,
                None,
                None,
                signature["file_size"],
                signature["modified_time_ns"],
            ),
        )

        connection.commit()


def mark_success(
    file_path,
    file_hash,
    chunk_count=0,
    metadata=None,
):
    """Mark a document as successfully processed."""

    path = Path(file_path).resolve()
    signature = get_file_signature(path)

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    metadata_json = (
        json.dumps(
            metadata,
            ensure_ascii=False,
        )
        if metadata is not None
        else None
    )

    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                path,
                filename,
                extension,
                modified_time,
                file_hash,
                ingestion_status,
                chunk_count,
                last_ingested_at,
                error_message,
                metadata_json,
                file_size,
                modified_time_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                chunk_count = excluded.chunk_count,
                last_ingested_at = excluded.last_ingested_at,
                error_message = NULL,
                metadata_json = excluded.metadata_json,
                file_size = excluded.file_size,
                modified_time_ns = excluded.modified_time_ns
            """,
            (
                signature["path"],
                signature["filename"],
                signature["extension"],
                signature["modified_time"],
                file_hash,
                "success",
                chunk_count,
                timestamp,
                None,
                metadata_json,
                signature["file_size"],
                signature["modified_time_ns"],
            ),
        )

        connection.commit()


def mark_failed(
    file_path,
    file_hash,
    error_message,
):
    """Record a failed ingestion attempt."""

    path = Path(file_path).resolve()
    signature = get_file_signature(path)

    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                path,
                filename,
                extension,
                modified_time,
                file_hash,
                ingestion_status,
                chunk_count,
                last_ingested_at,
                error_message,
                metadata_json,
                file_size,
                modified_time_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                error_message = excluded.error_message,
                file_size = excluded.file_size,
                modified_time_ns = excluded.modified_time_ns
            """,
            (
                signature["path"],
                signature["filename"],
                signature["extension"],
                signature["modified_time"],
                file_hash,
                "failed",
                0,
                None,
                str(error_message),
                None,
                signature["file_size"],
                signature["modified_time_ns"],
            ),
        )

        connection.commit()


def remove_document(file_path):
    """Remove a document from the registry."""

    file_path = str(Path(file_path).resolve())

    with _get_connection() as connection:
        connection.execute(
            """
            DELETE FROM documents
            WHERE path = ?
            """,
            (file_path,),
        )

        connection.commit()


def get_documents_by_status(status):
    """Return all documents with the requested ingestion status."""

    initialize_registry()

    with _get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE ingestion_status = ?
            ORDER BY filename
            """,
            (status,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_document_summary():
    """Return counts grouped by ingestion status."""

    initialize_registry()

    with _get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                ingestion_status,
                COUNT(*) AS count
            FROM documents
            GROUP BY ingestion_status
            ORDER BY ingestion_status
            """
        ).fetchall()

    return {
        row["ingestion_status"]: row["count"]
        for row in rows
    }


def search_documents(search_term):
    """
    Search registered filenames and paths.

    This is file-level discovery, not semantic retrieval.
    """

    initialize_registry()

    pattern = f"%{search_term}%"

    with _get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE filename LIKE ?
               OR path LIKE ?
            ORDER BY filename
            """,
            (pattern, pattern),
        ).fetchall()

    return [dict(row) for row in rows]

def register_media(file_path, file_hash, media_metadata):
    """Register a media file with lightweight metadata."""

    path = Path(file_path).resolve()
    signature = get_file_signature(path)

    metadata_json = json.dumps(
        media_metadata,
        ensure_ascii=False,
    )

    with _get_connection() as connection:
        connection.execute(
            """
            INSERT INTO documents (
                path,
                filename,
                extension,
                modified_time,
                file_hash,
                ingestion_status,
                chunk_count,
                last_ingested_at,
                error_message,
                metadata_json,
                file_size,
                modified_time_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                last_ingested_at = excluded.last_ingested_at,
                error_message = NULL,
                metadata_json = excluded.metadata_json,
                file_size = excluded.file_size,
                modified_time_ns = excluded.modified_time_ns
            """,
            (
                signature["path"],
                signature["filename"],
                signature["extension"],
                signature["modified_time"],
                file_hash,
                "success",
                0,
                datetime.now(timezone.utc).isoformat(),
                None,
                metadata_json,
                signature["file_size"],
                signature["modified_time_ns"],
            ),
        )

        connection.commit()