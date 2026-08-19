from vectordb.chroma_db import collection

from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection



def retrieve_multimodal(embedding, top_k=20):
    """
    Retrieve the most semantically relevant candidates across
    document and media knowledge.

    The retriever is modality-agnostic: it does not assume whether
    the query refers to people, files, images, objects, or content.
    """

    if embedding is None:
        return []

    query_embedding = embedding.tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    candidates = []

    for document, metadata, distance in zip(
        documents,
        metadatas,
        distances,
    ):
        candidates.append({
            "document": document,
            "metadata": metadata,
            "distance": distance,
            "source": metadata.get("source"),
            "file_path": metadata.get("file_path"),
            "document_type": metadata.get("document_type"),
            "media_type": metadata.get("media_type"),
            "people": metadata.get("people"),
        })

    return candidates


def retrieve_multimodal(embedding, top_k=20):
    if embedding is None:
        return []

    results = collection.query(
        query_embeddings=[embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    return [
        {
            "document": document,
            "metadata": metadata,
            "distance": distance,
            "source": metadata.get("source"),
            "file_path": metadata.get("file_path"),
            "document_type": metadata.get("document_type"),
            "media_type": metadata.get("media_type"),
            "people": metadata.get("people"),
        }
        for document, metadata, distance in zip(
            documents,
            metadatas,
            distances,
        )
    ]


def search_multimodal(query, top_k=20):
    """Search across all semantically embedded LifeOS knowledge."""
    if not str(query).strip():
        return []

    embedding = generate_embeddings([query])[0]

    return retrieve_multimodal(
        embedding,
        top_k=top_k,
    )