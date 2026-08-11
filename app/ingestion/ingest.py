"""
ingest.py

LifeOS incremental ingestion pipeline.

Pipeline:

1. Recursively discover supported files.
2. Detect deleted files.
3. Use cheap filesystem metadata to detect possible changes.
4. Calculate SHA-256 only for new/changed files.
5. Skip files whose content is actually unchanged.
6. Route structured files to their specialized handler.
7. Process text documents through:
   read -> clean -> chunk -> embed -> ChromaDB
8. Update the registry only after successful processing.
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
    file_needs_processing,
    is_unchanged,
    mark_processing,
    mark_success,
    mark_failed,
    remove_document,
)


logger = logging.getLogger(__name__)


def _discover_files(documents_dir):
    """
    Recursively discover supported files.

    Returns:
        dict[str, Path]
    """

    current_files = {}

    for file_path in documents_dir.rglob("*"):

        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        resolved_path = file_path.resolve()

        current_files[str(resolved_path)] = resolved_path

    return current_files


def _remove_deleted_files(current_files):
    """
    Remove registry and ChromaDB entries for files that no longer exist.
    """

    registered_documents = get_all_documents()

    current_paths = set(current_files.keys())

    for document in registered_documents:

        registered_path = Path(
            document["path"]
        ).resolve()

        if str(registered_path) in current_paths:
            continue

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


def _process_structured_file(file_path, file_hash):
    """
    Inspect a structured file and store its metadata in the registry.

    Structured files are NOT embedded into ChromaDB.
    """

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


def _process_text_document(file_path, file_hash):
    """
    Process a normal text/document file.

    read -> clean -> chunk -> embed -> ChromaDB
    """

    mark_processing(
        file_path,
        file_hash,
    )

    text = read_documents(
        str(file_path)
    )

    cleaned_text = clean_text(
        text
    )

    chunks = chunk_text(
        cleaned_text
    )

    if not chunks:
        raise ValueError(
            f"No text extracted from {file_path.name}"
        )

    embeddings = generate_embeddings(
        chunks
    )

    store_embeddings(
        chunks,
        embeddings,
        str(file_path),
    )

    mark_success(
        file_path,
        file_hash,
        chunk_count=len(chunks),
    )

    logger.info(
        "Successfully ingested %s (%d chunks)",
        file_path.name,
        len(chunks),
    )


def ingest_documents(
    documents_dir=DOCUMENTS_DIR,
    include_csv=False,
):
    """
    Incrementally ingest the LifeOS knowledge directory.

    include_csv is retained for backwards compatibility.
    Structured files are routed through their specialized handler.
    """

    documents_dir = Path(
        documents_dir
    ).resolve()

    documents_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    initialize_registry()

    logger.info(
        "Starting incremental ingestion: %s",
        documents_dir,
    )

    # ---------------------------------------------------------
    # 1. DISCOVER
    # ---------------------------------------------------------

    current_files = _discover_files(
        documents_dir
    )

    logger.info(
        "Discovered %d supported files",
        len(current_files),
    )

    # ---------------------------------------------------------
    # 2. DELETE SYNCHRONIZATION
    # ---------------------------------------------------------

    _remove_deleted_files(
        current_files
    )

    # ---------------------------------------------------------
    # 3. PROCESS NEW / CHANGED FILES
    # ---------------------------------------------------------

    for file_path in current_files.values():

        try:

            # -------------------------------------------------
            # Cheap check first.
            #
            # This only checks filesystem metadata.
            # No file contents are read.
            # -------------------------------------------------

            if not file_needs_processing(
                file_path
            ):

                logger.debug(
                    "Unchanged, skipping: %s",
                    file_path.name,
                )

                continue

            logger.info(
                "New or potentially changed file: %s",
                file_path.name,
            )

            # -------------------------------------------------
            # Classify before expensive processing.
            # -------------------------------------------------

            file_info = classify_file(
                file_path
            )

            logger.info(
                "Classification: %s | type=%s | strategy=%s | size=%.2f MB",
                file_path.name,
                file_info["type"],
                file_info["strategy"],
                file_info["size_mb"],
            )

            if file_info["strategy"] == "unsupported":

                logger.warning(
                    "Unsupported file type, skipping: %s",
                    file_path,
                )

                continue

            # -------------------------------------------------
            # SHA-256 is calculated ONLY when the cheap metadata
            # check says the file is new or potentially changed.
            # -------------------------------------------------

            file_hash = calculate_file_hash(
                file_path
            )

            # -------------------------------------------------
            # Metadata may have changed while the content stayed
            # identical. Avoid unnecessary re-embedding.
            # -------------------------------------------------

            if is_unchanged(
                file_path,
                file_hash,
            ):

                logger.info(
                    "Content unchanged, skipping: %s",
                    file_path.name,
                )

                continue

            # -------------------------------------------------
            # STRUCTURED DATA
            # -------------------------------------------------

            if file_info["type"] == "structured_data":

                _process_structured_file(
                    file_path,
                    file_hash,
                )

                continue

            # -------------------------------------------------
            # TEXT DOCUMENT
            # -------------------------------------------------

            if file_info["type"] == "text_document":

                _process_text_document(
                    file_path,
                    file_hash,
                )

                continue

            # -------------------------------------------------
            # SAFETY FALLBACK
            # -------------------------------------------------

            logger.warning(
                "No ingestion strategy implemented for: %s",
                file_path,
            )

        except FileNotFoundError:

            # A file can disappear while the scanner is running.
            logger.info(
                "File disappeared during ingestion: %s",
                file_path,
            )

            continue

        except Exception as error:

            logger.exception(
                "Failed to ingest %s: %s",
                file_path.name,
                error,
            )

            try:

                # file_hash may not exist if hashing failed.
                # In that case calculate it is not useful, so use
                # an empty marker for the failed registry record.
                failed_hash = locals().get(
                    "file_hash",
                    "",
                )

                mark_failed(
                    file_path,
                    failed_hash,
                    error,
                )

            except Exception:

                logger.exception(
                    "Failed to update registry for %s",
                    file_path.name,
                )

    logger.info(
        "Incremental ingestion completed."
    )


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    ingest_documents()