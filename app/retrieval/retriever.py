from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import collection

def retrieve(query,top_k=3):

    query_embedding = generate_embeddings([query])[0]    # convert query to vector embeddings.

    results = collection.query(
        query_embeddings = [query_embedding.tolist()],    # Search ChromaDB.
        n_results=top_k
    )
    return results