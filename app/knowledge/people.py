import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

from config import DATA_DIR


PEOPLE_DB = Path(DATA_DIR) / "people_registry.db"


def _connect():
    connection = sqlite3.connect(PEOPLE_DB)
    connection.row_factory = sqlite3.Row
    return connection


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

def register_unknown_faces(file_path):
    """
    Detect all faces in an image and register each as an unknown person.

    No identity is assigned automatically.
    """
    from knowledge.face import extract_faces

    initialize_people_registry()

    faces = extract_faces(file_path)
    registered = []

    for face in faces:
        person_id = create_person(
            label=None,
            is_user=False,
            status="unknown",
        )

        add_face_embedding(
            person_id,
            face["embedding"],
            source_path=file_path,
        )

        registered.append(
            {
                "person_id": person_id,
                "bbox": face["bbox"],
                "det_score": face["det_score"],
            }
        )

    return registered

def match_face(embedding, threshold=0.45):
    """
    Match a face embedding against known stored embeddings.

    Returns the best person match or None.
    """

    initialize_people_registry()

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