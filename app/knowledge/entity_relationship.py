import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from config import DATA_DIR, ENTITY_DB
except ImportError:
    from app.config import DATA_DIR, ENTITY_DB

try:
    from storage.sqlite import connect_sqlite
except ImportError:
    from app.storage.sqlite import connect_sqlite


def _connect():
    return connect_sqlite(
        ENTITY_DB,
        enable_foreign_keys=True,
        timeout=10.0,
        wal_mode=True,
    )


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
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(entity_id, alias) ON CONFLICT IGNORE,
                FOREIGN KEY(entity_id)
                    REFERENCES entities(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entities_lookup
            ON entities(entity_type COLLATE NOCASE, canonical_name COLLATE NOCASE)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_aliases_lookup
            ON entity_aliases(alias COLLATE NOCASE)
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

    # Extract aliases if provided in attributes
    raw_aliases = attributes.get("aliases", [])
    if isinstance(raw_aliases, (list, tuple, set)):
        aliases = [str(a).strip() for a in raw_aliases if a and str(a).strip()]
    else:
        aliases = []

    with _connect() as connection:
        # Check for existing entity case-insensitively
        existing = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE entity_type = ? COLLATE NOCASE
              AND canonical_name = ? COLLATE NOCASE
            """,
            (entity_type, canonical_name),
        ).fetchone()

        if existing:
            entity_id = existing["id"]
            existing_attrs = json.loads(existing["attributes"])
            merged_attrs = {**existing_attrs, **attributes}
            # Merge aliases
            existing_aliases = set(existing_attrs.get("aliases", []))
            existing_aliases.update(aliases)
            if existing_aliases:
                merged_attrs["aliases"] = sorted(existing_aliases)

            connection.execute(
                """
                UPDATE entities
                SET attributes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(merged_attrs, ensure_ascii=False),
                    now,
                    entity_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO entities (
                    entity_type,
                    canonical_name,
                    attributes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entity_type,
                    canonical_name,
                    json.dumps(attributes, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            entity_id = cursor.lastrowid

        # Insert aliases into entity_aliases table
        for alias in aliases:
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_aliases (
                    entity_id,
                    alias,
                    created_at
                )
                VALUES (?, ?, ?)
                """,
                (entity_id, alias, now),
            )

        row = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE id = ?
            """,
            (entity_id,),
        ).fetchone()

        connection.commit()

    return _entity_from_row(row)


def add_entity_alias(entity_id, alias):
    """Add an alias for an existing entity."""
    if not alias or not str(alias).strip():
        raise ValueError("Alias cannot be empty.")

    alias = str(alias).strip()
    initialize_entity_store()

    entity = get_entity(entity_id)
    if not entity:
        raise ValueError(f"Entity with ID {entity_id} does not exist.")

    now = datetime.now(timezone.utc).isoformat()

    with _connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO entity_aliases (
                entity_id,
                alias,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (int(entity_id), alias, now),
        )

        # Update attributes in entities table to keep aliases in sync
        attrs = dict(entity.get("attributes", {}))
        current_aliases = set(attrs.get("aliases", []))
        current_aliases.add(alias)
        attrs["aliases"] = sorted(current_aliases)

        connection.execute(
            """
            UPDATE entities
            SET attributes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(attrs, ensure_ascii=False),
                now,
                int(entity_id),
            ),
        )
        connection.commit()

    return get_entity(entity_id)


def get_entity_aliases(entity_id):
    """Retrieve all aliases for an entity."""
    initialize_entity_store()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT alias
            FROM entity_aliases
            WHERE entity_id = ?
            ORDER BY alias
            """,
            (int(entity_id),),
        ).fetchall()

    return [row["alias"] for row in rows]


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

    parameters = []

    if name is not None:
        clean_name = str(name).strip()
        query = """
            SELECT DISTINCT e.*
            FROM entities e
            LEFT JOIN entity_aliases a ON e.id = a.entity_id
            WHERE (e.canonical_name = ? COLLATE NOCASE OR a.alias = ? COLLATE NOCASE)
        """
        parameters.extend([clean_name, clean_name])

        if entity_type is not None:
            query += " AND e.entity_type = ? COLLATE NOCASE"
            parameters.append(str(entity_type).strip())

        query += " ORDER BY e.entity_type, e.canonical_name"
    else:
        query = """
            SELECT *
            FROM entities
            WHERE 1 = 1
        """

        if entity_type is not None:
            query += " AND entity_type = ? COLLATE NOCASE"
            parameters.append(str(entity_type).strip())

        query += " ORDER BY entity_type, canonical_name"

    with _connect() as connection:
        rows = connection.execute(
            query,
            parameters,
        ).fetchall()

    return [_entity_from_row(row) for row in rows]


def resolve_entity(name, entity_type=None):
    """
    Resolve a query name or alias to entity candidates with ambiguity detection.

    Returns:
        dict: {
            "status": "exact" | "alias" | "ambiguous" | "not_found",
            "entity": matched entity dict or None,
            "matches": list of matching entity dicts
        }
    """
    if not name or not str(name).strip():
        return {
            "status": "not_found",
            "entity": None,
            "matches": [],
        }

    clean_name = str(name).strip().lower()
    matches = find_entities(entity_type=entity_type, name=name)

    if not matches:
        return {
            "status": "not_found",
            "entity": None,
            "matches": [],
        }

    # Check for exact canonical match
    exact_canonical = [
        m for m in matches
        if m["canonical_name"].strip().lower() == clean_name
    ]

    if len(exact_canonical) == 1:
        return {
            "status": "exact",
            "entity": exact_canonical[0],
            "matches": matches,
        }
    elif len(exact_canonical) > 1:
        return {
            "status": "ambiguous",
            "entity": None,
            "matches": exact_canonical,
        }

    # If no exact canonical match, check if unique alias match
    if len(matches) == 1:
        return {
            "status": "alias",
            "entity": matches[0],
            "matches": matches,
        }

    return {
        "status": "ambiguous",
        "entity": None,
        "matches": matches,
    }


def link_person_to_entity(person_id, canonical_name=None, attributes=None):
    """
    Link a person from people_registry.db to an entity record in entities.db.

    Preserves separation between people.db and entities.db using person_id.
    """
    from knowledge.people import get_person

    person = get_person(person_id)
    if not person:
        raise ValueError(f"Person with ID {person_id} not found in people registry.")

    name = canonical_name or person.get("label") or f"Person_{str(person_id)[:8]}"
    attrs = dict(attributes or {})
    attrs["person_id"] = str(person_id)
    attrs["is_user"] = bool(person.get("is_user", 0))
    attrs["status"] = person.get("status", "unknown")

    existing = get_entity_by_person_id(person_id)
    if existing:
        merged_attrs = {**existing["attributes"], **attrs}
        now = datetime.now(timezone.utc).isoformat()
        with _connect() as connection:
            connection.execute(
                """
                UPDATE entities
                SET canonical_name = ?, attributes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(name).strip(),
                    json.dumps(merged_attrs, ensure_ascii=False),
                    now,
                    existing["id"],
                ),
            )
            connection.commit()
        return get_entity(existing["id"])

    return create_entity("person", name, attrs)


def get_entity_by_person_id(person_id):
    """Find the entity record associated with a person_id."""
    initialize_entity_store()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE entity_type = 'person' COLLATE NOCASE
            """
        ).fetchall()

    for row in rows:
        entity = _entity_from_row(row)
        if entity.get("attributes", {}).get("person_id") == str(person_id):
            return entity
    return None


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
        connection.execute("DELETE FROM entity_aliases")
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