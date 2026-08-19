"""
ingest.py

LifeOS incremental ingestion pipeline.

Supports:

1. Full/incremental directory synchronization.
2. Targeted single-file ingestion.
3. Targeted file deletion.
4. Cheap metadata-based change detection.
5. SHA-256 only for new/changed files.
6. Structured-file handling.
7. Text/document embedding.
8. Registry synchronization.
"""
from pathlib import Path
import logging

from config import DOCUMENTS_DIR, SUPPORTED_EXTENSIONS

from ingestion.reader import read_documents
from ingestion.file_classifier import classify_file

from processing.chunker import clean_text, chunk_text
from embeddings.embedder import generate_embeddings

from ingestion.structured.handler import handle_structured_file

from vectordb.chroma_db import store_media_description


from llm.generator import (
    analyze_image,
    analyze_image_metadata,
)

from knowledge.people import get_people_metadata

from vectordb.chroma_db import (
    store_embeddings,
    delete_document,
)

from knowledge.media import (
    is_media_file,
    get_media_metadata,
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
    register_media,
)



logger = logging.getLogger(__name__)


def _discover_files(documents_dir):
    """Recursively discover supported files."""

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
    """Synchronize registry/ChromaDB with files deleted from disk."""

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
            remove_file(
                registered_path
            )

        except Exception as error:
            logger.exception(
                "Failed to remove deleted document %s: %s",
                document["filename"],
                error,
            )


def _process_structured_file(file_path, file_hash):
    """Process a structured file."""

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
    """Process a normal text/document file."""

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


def ingest_file(file_path):
    """
    Incrementally ingest ONE file.

    This is the hot path used by the filesystem watcher.

    It does not scan the entire documents directory.
    """

    path = Path(
        file_path
    ).resolve()

    if not path.exists() or not path.is_file():
        logger.info(
            "File no longer exists: %s",
            path,
        )
        return False

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.debug(
            "Unsupported file type, skipping: %s",
            path,
        )
        return False

    initialize_registry()

    try:

        # -----------------------------------------------------
        # CHEAP CHANGE CHECK
        # -----------------------------------------------------

        if not file_needs_processing(path):

            logger.debug(
                "Unchanged, skipping: %s",
                path.name,
            )

            return False

        logger.info(
            "Processing changed file: %s",
            path.name,
        )

        if is_media_file(file_path):
            media_metadata = get_media_metadata(file_path)
            file_hash = calculate_file_hash(file_path)

            if is_unchanged(file_path, file_hash):
                return False

            if media_metadata["media_type"] == "image":
                media_metadata["description"] = analyze_image(file_path)
                media_metadata["visual_metadata"] = analyze_image_metadata(file_path)

                description_embedding = generate_embeddings(
                    [media_metadata["description"]]
                )

                people_metadata = get_people_metadata(file_path)

                store_media_description(
                    file_path,
                    media_metadata["description"],
                    description_embedding[0],
                    people=people_metadata["people"],
                )

            register_media(
                file_path,
                file_hash,
                media_metadata,
            )

            logger.info(
                "Media file registered: %s | type=%s",
                file_path.name,
                media_metadata["media_type"],
            )

            return True

        # -----------------------------------------------------
        # CLASSIFY
        # -----------------------------------------------------

        file_info = classify_file(
            path
        )

        logger.info(
            "Classification: %s | type=%s | strategy=%s | size=%.2f MB",
            path.name,
            file_info["type"],
            file_info["strategy"],
            file_info["size_mb"],
        )

        if file_info["strategy"] == "unsupported":

            logger.warning(
                "Unsupported file type, skipping: %s",
                path,
            )

            return False

        # -----------------------------------------------------
        # HASH ONLY THIS FILE
        # -----------------------------------------------------

        file_hash = calculate_file_hash(
            path
        )

        # -----------------------------------------------------
        # CONTENT DID NOT ACTUALLY CHANGE
        # -----------------------------------------------------

        if is_unchanged(
            path,
            file_hash,
        ):

            logger.info(
                "Content unchanged, skipping: %s",
                path.name,
            )

            return False



        # -----------------------------------------------------
        # STRUCTURED DATA
        # -----------------------------------------------------

        if file_info["type"] == "structured_data":

            _process_structured_file(
                path,
                file_hash,
            )

            return True

        # -----------------------------------------------------
        # TEXT DOCUMENT
        # -----------------------------------------------------

        if file_info["type"] == "text_document":

            _process_text_document(
                path,
                file_hash,
            )

            return True

        logger.warning(
            "No ingestion strategy implemented for: %s",
            path,
        )

        return False

    except FileNotFoundError:

        logger.info(
            "File disappeared during ingestion: %s",
            path,
        )

        return False

    except Exception as error:

        logger.exception(
            "Failed to ingest %s: %s",
            path.name,
            error,
        )

        try:

            failed_hash = locals().get(
                "file_hash",
                "",
            )

            mark_failed(
                path,
                failed_hash,
                error,
            )

        except Exception:

            logger.exception(
                "Failed to update registry for %s",
                path.name,
            )

        return False


def remove_file(file_path):
    """
    Remove ONE file from ChromaDB and the document registry.

    Used by the filesystem watcher when a file disappears.
    """

    path = Path(
        file_path
    ).resolve()

    logger.info(
        "Removing document: %s",
        path.name,
    )

    try:

        delete_document(
            path
        )

        remove_document(
            path
        )

        logger.info(
            "Document removed successfully: %s",
            path.name,
        )

        return True

    except Exception as error:

        logger.exception(
            "Failed to remove document %s: %s",
            path.name,
            error,
        )

        return False


def ingest_documents(
    documents_dir=DOCUMENTS_DIR,
    include_csv=False,
):
    """
    Full/incremental directory synchronization.

    This is used for:
    - initial indexing
    - manual reindexing
    - recovery
    - periodic consistency checks

    Normal filesystem events should use ingest_file()
    and remove_file() instead.
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
    # DISCOVER
    # ---------------------------------------------------------

    current_files = _discover_files(
        documents_dir
    )

    logger.info(
        "Discovered %d supported files",
        len(current_files),
    )

    # ---------------------------------------------------------
    # DELETE SYNCHRONIZATION
    # ---------------------------------------------------------

    _remove_deleted_files(
        current_files
    )

    # ---------------------------------------------------------
    # PROCESS EACH NEW/CHANGED FILE
    # ---------------------------------------------------------

    for file_path in current_files.values():

        ingest_file(
            file_path
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