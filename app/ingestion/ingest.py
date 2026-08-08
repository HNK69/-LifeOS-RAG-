from pathlib import Path
import logging

from config import DOCUMENTS_DIR, SUPPORTED_EXTENSIONS
from ingestion.reader import read_documents
from processing.chunker import clean_text, chunk_text
from embeddings.embedder import generate_embeddings
from vectordb.chroma_db import store_embeddings


logger = logging.getLogger(__name__)


def ingest_documents(documents_dir=DOCUMENTS_DIR, include_csv=False):
    documents_dir = Path(documents_dir)

    for file_path in documents_dir.iterdir():

        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        if file_path.suffix.lower() == ".csv" and not include_csv:
            logger.info("Skipping CSV: %s", file_path.name)
            continue
        
        logger.info("Ingesting: %s", file_path.name)

        try:
            text = read_documents(str(file_path))
            cleaned_text = clean_text(text)
            chunks = chunk_text(cleaned_text)

            if not chunks:
                logger.warning(
                    "No text extracted from %s",
                    file_path.name
                )
                continue

            embeddings = generate_embeddings(chunks)

            store_embeddings(
                chunks,
                embeddings,
                str(file_path)
            )

        except Exception as error:
            logger.exception(
                "Failed to ingest %s: %s",
                file_path.name,
                error
            )