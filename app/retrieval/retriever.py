from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection
from retrieval.file_index import find_file


def retrieve(query, top_k=3):

    file_match = find_file(query)

    if file_match:
        return [{
            "document": "",
            "source": file_match["name"],
            "file_path": file_match["path"],
            "chunk_id": None,
            "distance": 0.0
        }]

    query_embedding = generate_embeddings([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=[
            "documents",
            "metadatas",
            "distances"
        ]
    )

    retrieved = []

    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        retrieved.append({
            "document": doc,
            "source": meta["source"],
            "file_path": meta["file_path"],
            "chunk_id": meta["chunk_id"],
            "distance": dist
        })

    for item in retrieved:
        item["keyword_score"] = keyword_score(
            query,
            item["document"]
        )

    retrieved.sort(
        key=lambda x: (x["keyword_score"], -x["distance"]),
        reverse=True
    )

    return retrieved