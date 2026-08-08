from pathlib import Path

from ingestion.reader import read_documents
from processing.chunker import clean_text, chunk_text
from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import store_embeddings


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
}


def ingest_documents(documents_dir="data/documents"):
    documents_dir = Path(documents_dir)

    for file_path in documents_dir.iterdir():

        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        print(f"Ingesting: {file_path.name}")

        text = read_documents(str(file_path))
        cleaned_text = clean_text(text)
        chunks = chunk_text(cleaned_text)

        if not chunks:
            continue

        embeddings = generate_embeddings(chunks)

        store_embeddings(
            chunks,
            embeddings,
            str(file_path)
        )