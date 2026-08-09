from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection
from retrieval.file_index import find_file
from retrieval.structured_retriever import retrieve_structured_files


def retrieve_chunks(query, top_k=10):
    """Retrieve semantic document chunks only."""

    query_embedding = generate_embeddings([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    retrieved = []

    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if not doc:
            continue

        retrieved.append({
            "document": doc,
            "source": meta.get("source"),
            "file_path": meta.get("file_path"),
            "chunk_id": meta.get("chunk_id"),
            "distance": dist,
            "type": "chunk",
        })

    return retrieved


def retrieve(query, top_k=3):
    """Retrieve relevant information for general queries."""

    structured_matches = retrieve_structured_files(query)

    if structured_matches:
        return [
            {
                "document": "",
                "source": item["filename"],
                "file_path": item["path"],
                "chunk_id": None,
                "distance": 0.0,
                "type": "structured_file",
                "metadata": item["metadata"],
            }
            for item in structured_matches
        ]

    file_match = find_file(query)

    if file_match:
        return [
            {
                "document": "",
                "source": file_match["name"],
                "file_path": file_match["path"],
                "chunk_id": None,
                "distance": 0.0,
                "type": "file",
            }
        ]

    return retrieve_chunks(query, top_k)