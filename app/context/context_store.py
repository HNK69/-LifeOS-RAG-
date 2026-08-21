import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR


CONTEXT_DB = Path(DATA_DIR) / "context.db"


def _connect():
    connection = sqlite3.connect(CONTEXT_DB)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_context_store():
    CONTEXT_DB.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                context_type TEXT NOT NULL DEFAULT 'general',
                updated_at TEXT NOT NULL,
                valid_from TEXT,
                valid_until TEXT
            )
            """
        )

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(context)"
            ).fetchall()
        }

        if "context_type" not in columns:
            connection.execute(
                """
                ALTER TABLE context
                ADD COLUMN context_type TEXT NOT NULL DEFAULT 'general'
                """
            )

        if "valid_from" not in columns:
            connection.execute(
                """
                ALTER TABLE context
                ADD COLUMN valid_from TEXT
                """
            )

        if "valid_until" not in columns:
            connection.execute(
                """
                ALTER TABLE context
                ADD COLUMN valid_until TEXT
                """
            )

        connection.commit()


def set_context(
    key,
    value,
    context_type="general",
    valid_from=None,
    valid_until=None,
):
    if not key or not str(key).strip():
        raise ValueError("Context key cannot be empty.")

    if not context_type or not str(context_type).strip():
        raise ValueError("Context type cannot be empty.")

    initialize_context_store()

    updated_at = datetime.now(timezone.utc).isoformat()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO context (
                key,
                value,
                context_type,
                updated_at,
                valid_from,
                valid_until
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                context_type = excluded.context_type,
                updated_at = excluded.updated_at,
                valid_from = excluded.valid_from,
                valid_until = excluded.valid_until
            """,
            (
                str(key),
                str(value),
                str(context_type),
                updated_at,
                valid_from,
                valid_until,
            ),
        )
        connection.commit()


def get_context(key, default=None):
    initialize_context_store()

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT value
            FROM context
            WHERE key = ?
            """,
            (str(key),),
        ).fetchone()

    return row["value"] if row else default


def get_all_context():
    initialize_context_store()

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                key,
                value,
                context_type,
                updated_at,
                valid_from,
                valid_until
            FROM context
            ORDER BY key
            """
        ).fetchall()

    return {
        row["key"]: {
            "value": row["value"],
            "context_type": row["context_type"],
            "updated_at": row["updated_at"],
            "valid_from": row["valid_from"],
            "valid_until": row["valid_until"],
        }
        for row in rows
    }


def get_context_by_type(context_type):
    if not context_type or not str(context_type).strip():
        raise ValueError("Context type cannot be empty.")

    initialize_context_store()

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                key,
                value,
                context_type,
                updated_at,
                valid_from,
                valid_until
            FROM context
            WHERE context_type = ?
            ORDER BY key
            """,
            (str(context_type),),
        ).fetchall()

    return {
        row["key"]: {
            "value": row["value"],
            "context_type": row["context_type"],
            "updated_at": row["updated_at"],
            "valid_from": row["valid_from"],
            "valid_until": row["valid_until"],
        }
        for row in rows
    }


def clear_context():
    initialize_context_store()

    with _connect() as connection:
        connection.execute("DELETE FROM context")
        connection.commit()