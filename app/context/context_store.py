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

def get_active_context(at_time=None):
    """Return context entries active at the supplied timestamp."""
    initialize_context_store()

    if at_time is None:
        at_time = datetime.now(timezone.utc)

    if at_time.tzinfo is None:
        at_time = at_time.replace(tzinfo=timezone.utc)

    timestamp = at_time.isoformat()

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT key, value, context_type, updated_at,
                   valid_from, valid_until
            FROM context
            WHERE (valid_from IS NULL OR valid_from <= ?)
              AND (valid_until IS NULL OR valid_until >= ?)
            ORDER BY key
            """,
            (timestamp, timestamp),
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

def get_relevant_context(
    query,
    at_time=None,
    max_age_seconds=None,
):
    """Return active and relevant context, optionally filtered by freshness."""
    active = get_active_context(at_time)

    if at_time is None:
        at_time = datetime.now(timezone.utc)

    if at_time.tzinfo is None:
        at_time = at_time.replace(tzinfo=timezone.utc)

    query_terms = {
        term.lower()
        for term in str(query).replace("/", " ").split()
        if len(term) > 2
    }

    relevant = {}

    for key, item in active.items():
        if max_age_seconds is not None:
            updated_at = datetime.fromisoformat(
                item["updated_at"]
            )

            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(
                    tzinfo=timezone.utc
                )

            age_seconds = (
                at_time - updated_at
            ).total_seconds()

            if age_seconds > max_age_seconds:
                continue

        searchable = " ".join(
            [
                str(key),
                str(item["value"]),
                str(item["context_type"]),
            ]
        ).lower()

        score = sum(
            1
            for term in query_terms
            if term in searchable
        )

        if score > 0:
            relevant[key] = {
                **item,
                "relevance_score": score,
            }

    return relevant

def _get_relationship_context(query):
    """Retrieve relationships for entity names explicitly present in the query."""
    from knowledge.relationship_retriever import (
        retrieve_relationships,
    )

    relationships = []

    words = [
        term.strip(".,?!:;()[]{}\"'")
        for term in str(query).split()
    ]

    candidates = []

    for index, word in enumerate(words):
        if len(word) <= 2:
            continue

        candidate = word

        # Preserve simple multi-word names such as "Alice Smith".
        if index + 1 < len(words):
            next_word = words[index + 1].strip(
                ".,?!:;()[]{}\"'"
            )

            if len(next_word) > 2:
                candidates.append(
                    f"{candidate} {next_word}"
                )

        candidates.append(candidate)

    seen = set()

    for entity_name in candidates:
        normalized = entity_name.lower()

        if normalized in seen:
            continue

        seen.add(normalized)

        matches = retrieve_relationships(
            entity_name=entity_name,
            direction="both",
        )

        for relationship in matches:
            if relationship not in relationships:
                relationships.append(relationship)

    return relationships

def compose_context(query, at_time=None):
    """Build structured personal and relationship context relevant to a query."""
    relevant = get_relevant_context(query, at_time)
    relevant = resolve_context_conflicts(relevant)

    by_type = {}

    for key, item in relevant.items():
        context_type = item["context_type"]
        by_type.setdefault(context_type, {})[key] = item

    relationship_context = _get_relationship_context(query)

    return {
        "query": str(query),
        "context": relevant,
        "by_type": by_type,
        "relationships": relationship_context,
        "count": len(relevant),
        "relationship_count": len(relationship_context),
    }

def resolve_context_conflicts(context):
    """Resolve conflicting context entries deterministically."""
    precedence = {
        "general": 0,
        "preference": 1,
        "routine": 2,
        "schedule": 3,
        "goal": 4,
    }

    resolved = {}

    for key, item in context.items():
        current = resolved.get(key)

        if current is None:
            resolved[key] = item
            continue

        current_priority = precedence.get(
            current["context_type"],
            0,
        )

        new_priority = precedence.get(
            item["context_type"],
            0,
        )

        if new_priority > current_priority:
            resolved[key] = item
        elif new_priority == current_priority:
            if item["updated_at"] > current["updated_at"]:
                resolved[key] = item

    return resolved