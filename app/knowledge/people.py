import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
try:
    from config import DATA_DIR, FACE_MATCH_THRESHOLD, PEOPLE_DB
except ImportError:
    from app.config import DATA_DIR, FACE_MATCH_THRESHOLD, PEOPLE_DB

try:
    from storage.sqlite import connect_sqlite
except ImportError:
    from app.storage.sqlite import connect_sqlite


def _connect():
    return connect_sqlite(
        PEOPLE_DB,
        enable_foreign_keys=True,
        timeout=10.0,
        wal_mode=True,
    )


def initialize_people_registry():
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS people (
                person_id TEXT PRIMARY KEY,
                label TEXT,
                is_user INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'unknown',
                created_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS face_embeddings (
                embedding_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                source_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(person_id) REFERENCES people(person_id)
            )
            """
        )

        connection.commit()


def create_person(
    label=None,
    is_user=False,
    status="unknown",
):
    person_id = str(uuid.uuid4())

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO people (
                person_id,
                label,
                is_user,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                person_id,
                label,
                int(is_user),
                status,
                created_at,
            ),
        )

        connection.commit()

    return person_id


def add_face_embedding(
    person_id,
    embedding,
    source_path=None,
):
    embedding_id = str(uuid.uuid4())

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    embedding_json = json.dumps(
        embedding.tolist()
    )

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO face_embeddings (
                embedding_id,
                person_id,
                embedding_json,
                source_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                embedding_id,
                person_id,
                embedding_json,
                str(source_path)
                if source_path
                else None,
                created_at,
            ),
        )

        connection.commit()

    return embedding_id


def get_person(person_id):
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM people
            WHERE person_id = ?
            """,
            (person_id,),
        ).fetchone()

    return dict(row) if row else None


def get_all_people():
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM people
            ORDER BY created_at
            """
        ).fetchall()

    return [dict(row) for row in rows]

def match_face(embedding, threshold=None):
    """Match a face embedding against all stored face embeddings."""

    if threshold is None:
        threshold = FACE_MATCH_THRESHOLD

    query = np.asarray(
        embedding,
        dtype=np.float32,
    )

    query_norm = np.linalg.norm(query)

    if query_norm == 0:
        return None

    best_match = None
    best_score = -1.0

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                embedding_id,
                person_id,
                embedding_json
            FROM face_embeddings
            """
        ).fetchall()

    for row in rows:
        stored = np.asarray(
            json.loads(row["embedding_json"]),
            dtype=np.float32,
        )

        stored_norm = np.linalg.norm(stored)

        if stored_norm == 0:
            continue

        similarity = float(
            np.dot(query, stored)
            / (query_norm * stored_norm)
        )

        if similarity > best_score:
            best_score = similarity
            best_match = {
                "person_id": row["person_id"],
                "embedding_id": row["embedding_id"],
                "similarity": similarity,
            }

    if (
        best_match is None
        or best_match["similarity"] < threshold
    ):
        return None

    return best_match

def label_person(person_id, label, is_user=False):
    """Assign a user-confirmed identity to an existing person."""

    if not label or not str(label).strip():
        raise ValueError("Person label cannot be empty.")

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE people
            SET label = ?, is_user = ?, status = 'confirmed'
            WHERE person_id = ?
            """,
            (
                str(label).strip(),
                int(is_user),
                person_id,
            ),
        )

        connection.commit()

    if cursor.rowcount > 0:
        try:
            from knowledge.entity_relationship import link_person_to_entity

            link_person_to_entity(person_id, canonical_name=str(label).strip())
        except Exception:
            pass

    return cursor.rowcount > 0

def create_user_identity(label):
    """Create a user-owned person identity after explicit confirmation."""
    return create_person(
        label=label,
        is_user=True,
        status="confirmed",
    )


def enroll_user_face(person_id, file_path):
    """Extract all faces from an enrollment photo and attach them to the user."""
    from knowledge.face import extract_faces

    person = get_person(person_id)

    if not person:
        raise ValueError("Person does not exist.")

    if not person["is_user"]:
        raise ValueError("Person is not marked as the user.")

    faces = extract_faces(file_path)

    if len(faces) != 1:
        raise ValueError(
            "Enrollment photo must contain exactly one face."
        )

    return add_face_embedding(
        person_id,
        faces[0]["embedding"],
        source_path=file_path,
    )

def enroll_user_faces(person_id, file_paths):
    """Enroll multiple photos for an existing user identity."""

    person = get_person(person_id)

    if not person:
        raise ValueError("Person does not exist.")

    if not person["is_user"]:
        raise ValueError("Person is not marked as the user.")

    results = []

    for file_path in file_paths:
        try:
            embedding_id = enroll_user_face(
                person_id,
                file_path,
            )

            results.append({
                "path": str(file_path),
                "embedding_id": embedding_id,
                "success": True,
            })

        except Exception as exc:
            results.append({
                "path": str(file_path),
                "embedding_id": None,
                "success": False,
                "error": str(exc),
            })

    return results

def score_face_against_person(embedding, person_id):
    """Return cosine similarity against every embedding for one person."""
    import numpy as np

    query = np.asarray(embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query)

    if query_norm == 0:
        return []

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT embedding_id, embedding_json
            FROM face_embeddings
            WHERE person_id = ?
            """,
            (person_id,),
        ).fetchall()

    scores = []

    for row in rows:
        stored = np.asarray(
            json.loads(row["embedding_json"]),
            dtype=np.float32,
        )
        stored_norm = np.linalg.norm(stored)

        if stored_norm == 0:
            continue

        similarity = float(
            np.dot(query, stored)
            / (query_norm * stored_norm)
        )

        scores.append({
            "embedding_id": row["embedding_id"],
            "similarity": similarity,
        })

    return scores

def analyze_image_faces(file_path):
    """Detect and persist identity for every face in an image."""
    from knowledge.face import extract_faces

    faces = extract_faces(file_path)
    results = []

    for index, face in enumerate(faces):
        identity = identify_or_register_face(
            face["embedding"],
            source_path=file_path,
        )

        results.append({
            "face_index": index,
            "bbox": face["bbox"],
            "det_score": face["det_score"],
            **identity,
        })

    return results

def identify_or_register_face(
    embedding,
    source_path=None,
    threshold=None,
):
    """Match a face to an existing person or create a new anonymous person."""

    match = match_face(
        embedding,
        threshold=threshold,
    )

    if match:
        person = get_person(match["person_id"])

        return {
            "person_id": match["person_id"],
            "label": person["label"] if person else None,
            "similarity": match["similarity"],
            "status": "matched",
            "created": False,
        }

    person_id = create_person(
        label=None,
        is_user=False,
        status="unknown",
    )

    add_face_embedding(
        person_id,
        embedding,
        source_path=source_path,
    )

    return {
        "person_id": person_id,
        "label": None,
        "similarity": None,
        "status": "unknown",
        "created": True,
    }

def confirm_person_identity(person_id, label):
    """Assign a user-confirmed label to an anonymous person."""
    return label_person(
        person_id=person_id,
        label=label,
        is_user=False,
    )

def get_person_labels_for_image(file_path):
    """Return confirmed/recognized people found in an image."""
    results = analyze_image_faces(file_path)

    people = []

    for item in results:
        if item["status"] == "matched" and item["person_id"]:
            person = get_person(item["person_id"])

            if person:
                people.append({
                    "person_id": person["person_id"],
                    "label": person["label"],
                    "similarity": item["similarity"],
                })

    return people

def get_people_metadata(file_path):
    """Return dynamic recognized-person metadata for an image."""
    people = get_person_labels_for_image(file_path)

    return {
        "people": [
            {
                "person_id": person["person_id"],
                "label": person["label"],
                "similarity": person["similarity"],
            }
            for person in people
        ]
    }


def delete_face_embeddings_for_source(source_path):
    """
    Remove all face embeddings associated with a source image path.
    If an anonymous unconfirmed person identity is left with zero embeddings,
    prune the orphaned person record while strictly preserving confirmed/user identities.
    """
    if not source_path:
        return 0

    path_str = str(source_path)
    try:
        resolved_path_str = str(Path(source_path).resolve())
    except Exception:
        resolved_path_str = path_str

    initialize_people_registry()

    with _connect() as connection:
        # Find all person_ids associated with this source path
        rows = connection.execute(
            """
            SELECT DISTINCT person_id
            FROM face_embeddings
            WHERE source_path = ? OR source_path = ?
            """,
            (path_str, resolved_path_str),
        ).fetchall()

        affected_person_ids = [row["person_id"] for row in rows]

        # Delete matching face embeddings
        cursor = connection.execute(
            """
            DELETE FROM face_embeddings
            WHERE source_path = ? OR source_path = ?
            """,
            (path_str, resolved_path_str),
        )
        deleted_count = cursor.rowcount

        # For affected persons, check if they have remaining embeddings or are confirmed
        for person_id in affected_person_ids:
            remaining_row = connection.execute(
                """
                SELECT COUNT(*) as count
                FROM face_embeddings
                WHERE person_id = ?
                """,
                (person_id,),
            ).fetchone()
            remaining = remaining_row["count"] if remaining_row else 0

            if remaining == 0:
                person = connection.execute(
                    """
                    SELECT is_user, label, status
                    FROM people
                    WHERE person_id = ?
                    """,
                    (person_id,),
                ).fetchone()

                if person:
                    # Only delete anonymous unconfirmed people with no label
                    if (
                        not person["is_user"]
                        and not person["label"]
                        and person["status"] == "unknown"
                    ):
                        connection.execute(
                            """
                            DELETE FROM people
                            WHERE person_id = ?
                            """,
                            (person_id,),
                        )

        connection.commit()
        return deleted_count
