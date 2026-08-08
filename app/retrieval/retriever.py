from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection

def retrieve(query,top_k=3):

    query_embedding = generate_embeddings([query])[0]    # convert query to vector embeddings.

    results = collection.query(
        query_embeddings = [query_embedding.tolist()],    # Search ChromaDB.
        n_results=top_k,
        include=[
            "documents",
            "metadatas",
            "distances"
        ]
    )
    retrieved=[]

    documents=results["documents"][0]
    metadatas=results["metadatas"][0]
    distances=results["distances"][0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        retrieved.append({
            "document": doc,
            "source": meta["source"],
            "file_path": meta["file_path"],
            "chunk_id": meta["chunk_id"],
            "distance": dist
        })



    return retrieved