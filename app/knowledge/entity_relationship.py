import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR


ENTITY_DB = Path(DATA_DIR) / "entities.db"


def _connect():
    connection = sqlite3.connect(ENTITY_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_entity_store():
    ENTITY_DB.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                attributes TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_type, canonical_name)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(
                    source_entity_id,
                    target_entity_id,
                    relationship_type
                ),
                FOREIGN KEY(source_entity_id)
                    REFERENCES entities(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(target_entity_id)
                    REFERENCES entities(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.commit()


def create_entity(entity_type, canonical_name, attributes=None):
    if not entity_type or not str(entity_type).strip():
        raise ValueError("Entity type cannot be empty.")

    if not canonical_name or not str(canonical_name).strip():
        raise ValueError("Entity name cannot be empty.")

    initialize_entity_store()

    now = datetime.now(timezone.utc).isoformat()
    attributes = attributes or {}

    if not isinstance(attributes, dict):
        raise ValueError("Entity attributes must be a dictionary.")

    entity_type = str(entity_type).strip()
    canonical_name = str(canonical_name).strip()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO entities (
                entity_type,
                canonical_name,
                attributes,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, canonical_name)
            DO UPDATE SET
                attributes = excluded.attributes,
                updated_at = excluded.updated_at
            """,
            (
                entity_type,
                canonical_name,
                json.dumps(attributes, ensure_ascii=False),
                now,
                now,
            ),
        )

        row = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE entity_type = ?
              AND canonical_name = ?
            """,
            (
                entity_type,
                canonical_name,
            ),
        ).fetchone()

        connection.commit()

    return _entity_from_row(row)


def get_entity(entity_id):
    initialize_entity_store()

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE id = ?
            """,
            (int(entity_id),),
        ).fetchone()

    return _entity_from_row(row) if row else None


def find_entities(entity_type=None, name=None):
    initialize_entity_store()

    query = """
        SELECT *
        FROM entities
        WHERE 1 = 1
    """

    parameters = []

    if entity_type is not None:
        query += " AND entity_type = ?"
        parameters.append(str(entity_type).strip())

    if name is not None:
        query += " AND canonical_name = ?"
        parameters.append(str(name).strip())

    query += " ORDER BY entity_type, canonical_name"

    with _connect() as connection:
        rows = connection.execute(
            query,
            parameters,
        ).fetchall()

    return [_entity_from_row(row) for row in rows]


def create_relationship(
    source_entity_id,
    target_entity_id,
    relationship_type,
    confidence=1.0,
    metadata=None,
):
    if not relationship_type or not str(relationship_type).strip():
        raise ValueError("Relationship type cannot be empty.")

    confidence = float(confidence)

    if not 0.0 <= confidence <= 1.0:
        raise ValueError(
            "Relationship confidence must be between 0 and 1."
        )

    metadata = metadata or {}

    if not isinstance(metadata, dict):
        raise ValueError(
            "Relationship metadata must be a dictionary."
        )

    initialize_entity_store()

    relationship_type = str(relationship_type).strip()
    now = datetime.now(timezone.utc).isoformat()

    with _connect() as connection:
        source = connection.execute(
            """
            SELECT id
            FROM entities
            WHERE id = ?
            """,
            (int(source_entity_id),),
        ).fetchone()

        target = connection.execute(
            """
            SELECT id
            FROM entities
            WHERE id = ?
            """,
            (int(target_entity_id),),
        ).fetchone()

        if source is None:
            raise ValueError("Source entity does not exist.")

        if target is None:
            raise ValueError("Target entity does not exist.")

        connection.execute(
            """
            INSERT INTO relationships (
                source_entity_id,
                target_entity_id,
                relationship_type,
                confidence,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                source_entity_id,
                target_entity_id,
                relationship_type
            )
            DO UPDATE SET
                confidence = excluded.confidence,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                int(source_entity_id),
                int(target_entity_id),
                relationship_type,
                confidence,
                json.dumps(metadata, ensure_ascii=False),
                now,
                now,
            ),
        )

        row = connection.execute(
            """
            SELECT
                r.*,
                source.entity_type AS source_type,
                source.canonical_name AS source_name,
                target.entity_type AS target_type,
                target.canonical_name AS target_name
            FROM relationships r
            JOIN entities source
                ON source.id = r.source_entity_id
            JOIN entities target
                ON target.id = r.target_entity_id
            WHERE r.source_entity_id = ?
              AND r.target_entity_id = ?
              AND r.relationship_type = ?
            """,
            (
                int(source_entity_id),
                int(target_entity_id),
                relationship_type,
            ),
        ).fetchone()

        connection.commit()

    return _relationship_from_row(row)


def get_relationships(entity_id, direction="both"):
    if direction not in {"outgoing", "incoming", "both"}:
        raise ValueError(
            "Direction must be outgoing, incoming, or both."
        )

    initialize_entity_store()

    clauses = []
    parameters = []

    if direction in {"outgoing", "both"}:
        clauses.append("r.source_entity_id = ?")
        parameters.append(int(entity_id))

    if direction in {"incoming", "both"}:
        clauses.append("r.target_entity_id = ?")
        parameters.append(int(entity_id))

    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                r.*,
                source.entity_type AS source_type,
                source.canonical_name AS source_name,
                target.entity_type AS target_type,
                target.canonical_name AS target_name
            FROM relationships r
            JOIN entities source
                ON source.id = r.source_entity_id
            JOIN entities target
                ON target.id = r.target_entity_id
            WHERE {" OR ".join(clauses)}
            ORDER BY r.relationship_type, r.id
            """,
            parameters,
        ).fetchall()

    return [_relationship_from_row(row) for row in rows]


def clear_entity_store():
    initialize_entity_store()

    with _connect() as connection:
        connection.execute("DELETE FROM relationships")
        connection.execute("DELETE FROM entities")
        connection.commit()


def _entity_from_row(row):
    return {
        "id": row["id"],
        "entity_type": row["entity_type"],
        "canonical_name": row["canonical_name"],
        "attributes": json.loads(row["attributes"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _relationship_from_row(row):
    return {
        "id": row["id"],
        "source_entity_id": row["source_entity_id"],
        "target_entity_id": row["target_entity_id"],
        "relationship_type": row["relationship_type"],
        "confidence": row["confidence"],
        "metadata": json.loads(row["metadata"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source": {
            "id": row["source_entity_id"],
            "entity_type": row["source_type"],
            "canonical_name": row["source_name"],
        },
        "target": {
            "id": row["target_entity_id"],
            "entity_type": row["target_type"],
            "canonical_name": row["target_name"],
        },
    }