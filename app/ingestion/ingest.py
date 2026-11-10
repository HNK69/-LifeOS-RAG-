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
try:
    from config import get_max_file_size_bytes
except ImportError:
    from app.config import get_max_file_size_bytes

try:
    from ingestion.guardrails import validate_document_path, check_file_size
except ImportError:
    from app.ingestion.guardrails import validate_document_path, check_file_size

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

        try:
            resolved_path = validate_document_path(
                file_path,
                base_dir=documents_dir,
                must_exist=True,
            )
        except (ValueError, FileNotFoundError):
            continue

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

    metadata = None
    try:
        from knowledge.analyzer import analyze_document

        metadata = analyze_document(
            file_path,
            text=cleaned_text,
            extract_graph=True,
        )
    except Exception as exc:
        logger.warning(
            "Document entity analysis failed for %s: %s",
            file_path.name,
            exc,
        )

    mark_success(
        file_path,
        file_hash,
        chunk_count=len(chunks),
        metadata=metadata,
    )

    logger.info(
        "Successfully ingested %s (%d chunks)",
        file_path.name,
        len(chunks),
    )


def ingest_file(file_path, force=False):
    """
    Incrementally ingest ONE file.

    This is the hot path used by the filesystem watcher.

    It does not scan the entire documents directory.
    """

    path = validate_document_path(
        file_path,
        base_dir=getattr(ingest, "DOCUMENTS_DIR", DOCUMENTS_DIR) if "ingest" in globals() else DOCUMENTS_DIR,
        must_exist=False,
    )

    try:
        from knowledge.coordination import is_reindexing_active
        if is_reindexing_active():
            logger.warning(
                "Rejecting ingest_file for %s: reindex is currently in progress",
                path.name,
            )
            return False
    except Exception:
        pass

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

    # Check file size limit before ANY expensive processing (hashing, reading, chunking)
    max_bytes = get_max_file_size_bytes(path)
    file_size = path.stat().st_size
    if file_size > max_bytes:
        logger.warning(
            "Rejecting oversized file %s: %d bytes exceeds limit %d bytes",
            path.name,
            file_size,
            max_bytes,
        )
        return False

    initialize_registry()

    try:

        # -----------------------------------------------------
        # CHEAP CHANGE CHECK
        # -----------------------------------------------------

        if not force and not file_needs_processing(path):

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

            if not force and is_unchanged(file_path, file_hash):
                return False

            if media_metadata["media_type"] == "image":
                # Clean any previous face embeddings for this image before re-detecting
                try:
                    from knowledge.people import delete_face_embeddings_for_source
                    delete_face_embeddings_for_source(file_path)
                except Exception:
                    pass

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
                    visual_metadata=media_metadata["visual_metadata"],
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

        if not force and is_unchanged(
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
    Remove ONE file from ChromaDB, face embeddings, and the document registry.

    Used by the filesystem watcher when a file disappears.
    """

    path = validate_document_path(
        file_path,
        base_dir=getattr(ingest, "DOCUMENTS_DIR", DOCUMENTS_DIR) if "ingest" in globals() else DOCUMENTS_DIR,
        must_exist=False,
    )

    try:
        from knowledge.coordination import is_reindexing_active
        if is_reindexing_active():
            logger.warning(
                "Rejecting remove_file for %s: reindex is currently in progress",
                path.name,
            )
            return False
    except Exception:
        pass

    logger.info(
        "Removing document: %s",
        path.name,
    )

    errors = []

    # 1. Remove ChromaDB chunks (handles text chunks and media descriptions)
    try:
        delete_document(path)
    except Exception as exc:
        logger.exception("Failed to delete ChromaDB chunks for %s: %s", path.name, exc)
        errors.append(exc)

    # 2. Remove derived face embeddings and prune orphaned anonymous identities
    try:
        from knowledge.people import delete_face_embeddings_for_source
        delete_face_embeddings_for_source(path)
    except Exception as exc:
        logger.exception("Failed to delete face embeddings for %s: %s", path.name, exc)
        errors.append(exc)

    # If derived stores failed to clean up, preserve the document registry record
    # so reconciliation (e.g. _remove_deleted_files or retry) still has the breadcrumb.
    if errors:
        err_msg = "; ".join(str(e) for e in errors)
        logger.error(
            "Partial cleanup failure for %s (%d errors); preserving registry record for retry: %s",
            path.name,
            len(errors),
            err_msg,
        )
        try:
            from knowledge.document_registry import mark_removal_failed
            mark_removal_failed(path, err_msg)
        except Exception:
            pass
        return False

    # 3. Derived stores cleaned successfully -> now remove registry record
    try:
        remove_document(path)
    except Exception as exc:
        logger.exception("Failed to remove document registry record for %s: %s", path.name, exc)
        try:
            from knowledge.document_registry import mark_removal_failed
            mark_removal_failed(path, str(exc))
        except Exception:
            pass
        return False

    logger.info(
        "Document removed successfully: %s",
        path.name,
    )
    return True


def ingest_documents(
    documents_dir=DOCUMENTS_DIR,
    include_csv=False,
    force=False,
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
            file_path,
            force=force,
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