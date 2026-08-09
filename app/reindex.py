import sys

sys.path.insert(0, "app")

from ingestion.ingest import ingest_documents
from vectordb.chroma_db import collection


print("Deleting old vector data...")

existing = collection.get()

ids = existing.get("ids", [])

if ids:
    collection.delete(ids=ids)

print("Resetting registry status...")

from knowledge.document_registry import get_all_documents, mark_processing

for doc in get_all_documents():
    mark_processing(
        doc["path"],
        doc["file_hash"],
    )

print("Rebuilding index...")

ingest_documents()