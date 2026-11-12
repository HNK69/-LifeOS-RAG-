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
import json

import chromadb

try:
    from config import CHROMA_DIR
except ImportError:
    from app.config import CHROMA_DIR


logger = logging.getLogger(__name__)


# Persistent ChromaDB client with telemetry disabled.
client = chromadb.PersistentClient(
    path=str(CHROMA_DIR),
    settings=chromadb.config.Settings(anonymized_telemetry=False),
)


# Active collection reference.
_collection = client.get_or_create_collection(
    name="lifeos_documents"
)


class _CollectionProxy:
    """
    Transparent proxy delegating dynamically to the active collection.
    Allows test isolation and dynamic collection switching without breaking
    module-level 'from vectordb.chroma_db import collection' references.
    """
    def __getattr__(self, name):
        return getattr(_collection, name)

    def __repr__(self):
        return repr(_collection)


collection = _CollectionProxy()


def set_collection(new_collection):
    """Update active collection reference for test isolation or reconfiguration."""
    global _collection
    _collection = new_collection


def reset_client(path=None, ephemeral=False):
    """Reset the client and active collection to a specific path or ephemeral store."""
    global client, _collection
    if ephemeral:
        client = chromadb.EphemeralClient(
            settings=chromadb.config.Settings(anonymized_telemetry=False)
        )
    else:
        target_path = str(path or CHROMA_DIR)
        client = chromadb.PersistentClient(
            path=target_path,
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
    _collection = client.get_or_create_collection(name="lifeos_documents")
    return _collection


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

    embeddings_list = (
        embeddings.tolist()
        if hasattr(embeddings, "tolist")
        else list(embeddings)
    )

    # First write the new version.
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings_list,
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

def store_media_description(
    file_path,
    description,
    embedding,
    people=None,
    visual_metadata=None,
):
        """
        Store an image's vision description as a searchable Chroma document.
        """

        path = Path(file_path).resolve()

        document_id = _document_id(path)
        chunk_id = f"{document_id}_media"

        metadata = {
            "chunk_id": 0,
            "people": json.dumps(people or []),
            "source": path.name,
            "file_path": str(path),
            "folder": path.parent.name,
            "parent_folder": (
                path.parent.parent.name
                if path.parent.parent
                else None
            ),
            "document_type": "media",
            "category": "image",
            "media_type": "image",
            "visual_metadata": json.dumps(
                visual_metadata or {}),
        }

        embedding_list = (
            embedding.tolist()
            if hasattr(embedding, "tolist")
            else list(embedding)
        )

        collection.upsert(
            ids=[chunk_id],
            documents=[description],
            embeddings=[embedding_list],
            metadatas=[metadata],
        )

        logger.info(
            "Stored media description for %s",
            path.name,
        )