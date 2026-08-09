"""
document_registry.py

Persistent registry for LifeOS documents.

Tracks:
- file identity
- filename
- extension
- modification time
- SHA-256 hash
- ingestion status
- chunk count
- structured metadata
- errors
"""

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from config import BASE_DIR


REGISTRY_DB = BASE_DIR / "data" / "document_registry.db"


def _get_connection():
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(REGISTRY_DB)
    connection.row_factory = sqlite3.Row

    return connection


def initialize_registry():
    """
    Create the registry database/table.

    The metadata_json column stores optional structured-file metadata.
    """

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
                metadata_json TEXT
            )
            """
        )

        # Existing databases created by the previous version may not
        # have metadata_json, so add it safely.
        columns = connection.execute(
            "PRAGMA table_info(documents)"
        ).fetchall()

        column_names = {
            column["name"]
            for column in columns
        }

        if "metadata_json" not in column_names:

            connection.execute(
                """
                ALTER TABLE documents
                ADD COLUMN metadata_json TEXT
                """
            )

        connection.commit()


def calculate_file_hash(file_path):
    """
    Calculate SHA-256 without loading the entire file into memory.

    Time: O(n), where n is file size.
    Space: O(1) with respect to file size.
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

    if row is None:
        return None

    return dict(row)


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


def is_unchanged(file_path, file_hash):
    """
    Return True only when the same file content was previously
    ingested successfully.
    """

    record = get_document(file_path)

    if record is None:
        return False

    return (
        record["file_hash"] == file_hash
        and record["ingestion_status"] == "success"
    )


def mark_processing(file_path, file_hash):
    """Mark a file as currently being processed."""

    path = Path(file_path).resolve()

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
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                error_message = NULL
            """,
            (
                str(path),
                path.name,
                path.suffix.lower(),
                path.stat().st_mtime,
                file_hash,
                "processing",
                0,
                None,
                None,
                None,
            ),
        )

        connection.commit()


def mark_success(
    file_path,
    file_hash,
    chunk_count=0,
    metadata=None,
):
    """
    Mark a document as successfully processed.

    metadata is optional and is primarily used for structured files.
    """

    path = Path(file_path).resolve()

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    metadata_json = (
        json.dumps(metadata, ensure_ascii=False)
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
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                extension = excluded.extension,
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                chunk_count = excluded.chunk_count,
                last_ingested_at = excluded.last_ingested_at,
                error_message = NULL,
                metadata_json = excluded.metadata_json
            """,
            (
                str(path),
                path.name,
                path.suffix.lower(),
                path.stat().st_mtime,
                file_hash,
                "success",
                chunk_count,
                timestamp,
                None,
                metadata_json,
            ),
        )

        connection.commit()


def mark_failed(file_path, file_hash, error_message):
    """Record a failed ingestion."""

    path = Path(file_path).resolve()

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
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(path) DO UPDATE SET
                modified_time = excluded.modified_time,
                file_hash = excluded.file_hash,
                ingestion_status = excluded.ingestion_status,
                error_message = excluded.error_message
            """,
            (
                str(path),
                path.name,
                path.suffix.lower(),
                path.stat().st_mtime,
                file_hash,
                "failed",
                0,
                None,
                str(error_message),
                None,
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
    """
    Return a lightweight registry summary.

    Does not load metadata_json, making it suitable for UI/API
    inspection as the registry grows.
    """

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

    Time: O(n) database scan.
    Space: O(r), where r is the number of matches.
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