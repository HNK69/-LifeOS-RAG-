import chromadb
from pathlib import Path
from config import CHROMA_DIR
import logging

logger = logging.getLogger(__name__)

client = chromadb.PersistentClient(path=str(CHROMA_DIR))

collection = client.get_or_create_collection(
    name="lifeos_documents"
)

def store_embeddings(chunks,embeddings,file_path):

    source = Path(file_path).stem

    ids = [
        f"{Path(file_path).stem}_{i}"
        for i in range(len(chunks))
    ]

    metadatas=[]

    for i in range (len(chunks)):
        metadatas.append({
            "chunk_id": i,
            "source": Path(file_path).name,
            "file_path": str(file_path),
        })

    # collection.delete(where={})
    # client.delete_collection(name="documents")
    # collection = client.create_collection(name="documents")

    collection.upsert(
        ids = ids,
        documents = chunks,
        embeddings = embeddings.tolist(),
        metadatas=metadatas
    )
    logger.info("Stored %d chunks in ChromaDB", len(chunks))