import chromadb

client = chromadb.PersistentClient(path="chroma_db")

collection = client.get_or_create_collection(
    name="lifeos_documents"
)

def store_embeddings(chunks,embeddings):

    ids = [f"doc_{i}" for i in range(len(chunks))]

    collection.add(
        ids = ids,
        documents = chunks,
        embeddings = embeddings.tolist()
    )

    print(f"Stored {len(chunks)} chunks in chromaDB.")