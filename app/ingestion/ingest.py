"""
ingest.py

Controls the document ingestion pipeline.

The pipeline supports:

1. File discovery
2. File classification
3. Hash-based change detection
4. Incremental ingestion
5. Text-document chunking and embedding
6. Structured-data routing
7. Deleted-file detection
8. Ingestion status tracking

Structured files are classified separately so they can later use
specialized processing instead of blindly being converted into text
and embedded.
"""

from pathlib import Path
import logging

from config import DOCUMENTS_DIR, SUPPORTED_EXTENSIONS

from ingestion.reader import read_documents
from ingestion.file_classifier import classify_file

from processing.chunker import clean_text, chunk_text
from embeddings.embedder import generate_embeddings

from ingestion.structured.handler import handle_structured_file

from vectordb.chroma_db import (
    store_embeddings,
    delete_document,
)

from knowledge.document_registry import (
    initialize_registry,
    calculate_file_hash,
    get_all_documents,
    is_unchanged,
    mark_processing,
    mark_success,
    mark_failed,
    remove_document,
)


logger = logging.getLogger(__name__)


def ingest_documents(
    documents_dir=DOCUMENTS_DIR,
    include_csv=False,
):
    """
    Incrementally ingest documents from the documents directory.

    Files are classified before processing.

    Text documents currently use the existing:
        read -> clean -> chunk -> embed -> ChromaDB

    Structured files are identified separately so that a dedicated
    structured-data ingestion strategy can be added later.
    """

    documents_dir = Path(documents_dir).resolve()

    # Make sure the document registry exists.
    initialize_registry()

    # ---------------------------------------------------------
    # STEP 1:
    # Discover supported files currently present on disk.
    # ---------------------------------------------------------

    current_files = {}

    for file_path in documents_dir.iterdir():

        if not file_path.is_file():
            continue

        extension = file_path.suffix.lower()

        if extension not in SUPPORTED_EXTENSIONS:
            continue

        current_files[str(file_path.resolve())] = file_path

    # ---------------------------------------------------------
    # STEP 2:
    # Detect documents that were previously registered but
    # have since been deleted from the documents directory.
    # ---------------------------------------------------------

    registered_documents = get_all_documents()

    for document in registered_documents:

        registered_path = Path(document["path"])

        if str(registered_path) not in current_files:

            logger.info(
                "Document deleted: %s",
                document["filename"],
            )

            try:

                delete_document(registered_path)
                remove_document(registered_path)

            except Exception as error:

                logger.exception(
                    "Failed to remove deleted document %s: %s",
                    document["filename"],
                    error,
                )

    # ---------------------------------------------------------
    # STEP 3:
    # Process every currently existing supported file.
    # ---------------------------------------------------------

    for file_path in current_files.values():

        extension = file_path.suffix.lower()

        # -----------------------------------------------------
        # STEP 4:
        # Classify the file before deciding how it should be
        # processed.
        # -----------------------------------------------------

        try:

            file_info = classify_file(file_path)

        except Exception as error:

            logger.exception(
                "Failed to classify %s: %s",
                file_path.name,
                error,
            )

            continue

        logger.info(
            "File classification: %s | type=%s | strategy=%s | size=%.2f MB",
            file_path.name,
            file_info["type"],
            file_info["strategy"],
            file_info["size_mb"],
        )

        # -----------------------------------------------------
        # Unsupported/unknown formats should never enter the
        # embedding pipeline.
        # -----------------------------------------------------

        if file_info["strategy"] == "unsupported":

            logger.warning(
                "Unsupported file strategy: %s",
                file_path.name,
            )

            continue

        # -----------------------------------------------------
        # CSV remains opt-in for now.
        #
        # The important architectural difference is that CSV
        # is now explicitly classified as structured data.
        #
        # A future structured-data handler can replace this
        # branch without redesigning the whole ingestion system.
        # -----------------------------------------------------

        if file_info["type"] == "structured_data":

            try:
                file_hash = calculate_file_hash(file_path)

                if is_unchanged(file_path, file_hash):

                    logger.info(
                        "Unchanged, skipping: %s",
                        file_path.name,
                    )

                    continue

                logger.info(
                    "Processing structured file: %s",
                    file_path.name,
                )

                mark_processing(
                    file_path,
                    file_hash,
                )

                metadata = handle_structured_file(
                    file_path
                )

                mark_success(
                    file_path,
                    file_hash,
                    chunk_count=0,
                    metadata=metadata,
                )

                logger.info(
                    "Structured file indexed: %s",
                    file_path.name,
                )

            except Exception as error:

                logger.exception(
                    "Failed to process structured file %s: %s",
                    file_path.name,
                    error,
                )

                try:
                    mark_failed(
                        file_path,
                        file_hash,
                        error,
                    )
                except Exception:
                    logger.exception(
                        "Failed to update registry for %s",
                        file_path.name,
                    )

            continue

        # -----------------------------------------------------
        # STEP 5:
        # Calculate content hash.
        #
        # This allows us to skip files whose contents have not
        # changed since their last successful ingestion.
        # -----------------------------------------------------

        try:

            file_hash = calculate_file_hash(file_path)

        except Exception as error:

            logger.exception(
                "Failed to hash %s: %s",
                file_path.name,
                error,
            )

            continue

        # -----------------------------------------------------
        # STEP 6:
        # Skip unchanged files.
        # -----------------------------------------------------

        if is_unchanged(file_path, file_hash):

            logger.info(
                "Unchanged, skipping: %s",
                file_path.name,
            )

            continue

        logger.info(
            "Ingesting new/modified document: %s",
            file_path.name,
        )

        try:

            # Mark the document as being processed.
            mark_processing(
                file_path,
                file_hash,
            )

            # -------------------------------------------------
            # STEP 7:
            # Read the document.
            # -------------------------------------------------

            text = read_documents(
                str(file_path),
            )

            # -------------------------------------------------
            # STEP 8:
            # Clean extracted text.
            # -------------------------------------------------

            cleaned_text = clean_text(text)

            # -------------------------------------------------
            # STEP 9:
            # Split the document into chunks.
            # -------------------------------------------------

            chunks = chunk_text(cleaned_text)

            if not chunks:

                raise ValueError(
                    f"No text extracted from {file_path.name}"
                )

            # -------------------------------------------------
            # STEP 10:
            # Generate embeddings.
            #
            # This only happens for new or modified documents.
            # -------------------------------------------------

            embeddings = generate_embeddings(chunks)

            # -------------------------------------------------
            # STEP 11:
            # Store the new document version in ChromaDB.
            # -------------------------------------------------

            store_embeddings(
                chunks,
                embeddings,
                str(file_path),
            )

            # -------------------------------------------------
            # STEP 12:
            # Only mark ingestion successful after ChromaDB
            # successfully stores the document.
            # -------------------------------------------------

            mark_success(
                file_path,
                file_hash,
                len(chunks),
            )

            logger.info(
                "Successfully ingested %s (%d chunks)",
                file_path.name,
                len(chunks),
            )

        except Exception as error:

            logger.exception(
                "Failed to ingest %s: %s",
                file_path.name,
                error,
            )

            try:

                mark_failed(
                    file_path,
                    file_hash,
                    error,
                )

            except Exception:

                logger.exception(
                    "Failed to update registry for %s",
                    file_path.name,
                )