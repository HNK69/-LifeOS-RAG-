"""
chroma_db.py

Handles storage and removal of document chunks in ChromaDB.

Important design:

When a document changes, new chunks are written first.
Only after the new chunks are successfully stored are the old
chunks removed.

This prevents a failed re-indexing operation from destroying the
previous working version.
"""

import hashlib
import logging
from pathlib import Path

import chromadb

from config import CHROMA_DIR


logger = logging.getLogger(__name__)


# Persistent ChromaDB client.
client = chromadb.PersistentClient(
    path=str(CHROMA_DIR)
)


# Collection containing LifeOS document chunks.
collection = client.get_or_create_collection(
    name="lifeos_documents"
)


def _document_id(file_path):
    """
    Create a stable ID for a physical document.

    The absolute path is hashed so two files with the same filename
    in different directories cannot collide.
    """

    resolved_path = str(Path(file_path).resolve())

    return hashlib.sha256(
        resolved_path.encode("utf-8")
    ).hexdigest()[:16]


def _chunk_ids(file_path, chunk_count):
    """
    Generate IDs for all chunks belonging to a document.
    """

    document_id = _document_id(file_path)

    return [
        f"{document_id}_{i}"
        for i in range(chunk_count)
    ]


def store_embeddings(chunks, embeddings, file_path):
    """
    Store a document's chunks and embeddings in ChromaDB.

    New chunks are inserted before old chunks are deleted.

    This makes document re-indexing safer.
    """

    path = Path(file_path).resolve()

    ids = _chunk_ids(path, len(chunks))

    metadatas = []

    for i in range(len(chunks)):

        metadatas.append(
            {
                "chunk_id": i,
                "source": path.name,
                "file_path": str(path),

                "folder": path.parent.name,

                "parent_folder": (
                    path.parent.parent.name
                    if path.parent.parent
                    else None
                ),

                "document_type": "",
                "category": "",
            }
        )

    # First write the new version.
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings.tolist(),
        metadatas=metadatas,
    )

    # Find any previous chunks belonging to this same document.
    existing = collection.get(
        where={
            "file_path": str(path)
        },
        include=[]
    )

    existing_ids = set(existing.get("ids", []))

    # Delete old chunks that are no longer part of the new version.
    stale_ids = existing_ids - set(ids)

    if stale_ids:

        collection.delete(
            ids=list(stale_ids)
        )

        logger.info(
            "Removed %d stale chunks from %s",
            len(stale_ids),
            path.name
        )

    logger.info(
        "Stored %d chunks for %s",
        len(chunks),
        path.name
    )


def delete_document(file_path):
    """
    Remove every ChromaDB chunk belonging to a document.

    Used when a file has been deleted from the documents directory.
    """

    path = Path(file_path).resolve()

    result = collection.get(
        where={
            "file_path": str(path)
        },
        include=[]
    )

    ids = result.get("ids", [])

    if not ids:
        logger.info(
            "No ChromaDB chunks found for deleted document: %s",
            path.name
        )
        return

    collection.delete(ids=ids)

    logger.info(
        "Deleted %d chunks for %s",
        len(ids),
        path.name
    )