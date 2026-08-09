"""
file_classifier.py

Classifies files before ingestion.

The classifier does NOT decide how the file is ultimately retrieved.
It only determines the appropriate ingestion strategy.

This keeps the architecture extensible for future file types and
large files without hard-coding behavior around one particular dataset.
"""

from pathlib import Path


# Files that are naturally suited to normal text extraction,
# chunking, and embedding.
TEXT_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".pdf",
    ".docx",
}


# Structured formats should eventually have specialized processing.
# We identify them separately instead of blindly converting them
# into large blocks of text.
STRUCTURED_EXTENSIONS = {
    ".csv",
    ".json",
}


def classify_file(file_path, large_file_threshold_mb=10):
    """
    Classify a file based on its extension and size.

    Returns a dictionary describing the file and its recommended
    ingestion strategy.

    This function is intentionally generic. It does not contain
    assumptions about a specific dataset or filename.
    """

    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File does not exist: {path}"
        )

    extension = path.suffix.lower()

    # File size is calculated in bytes and converted to MB only
    # for the returned metadata.
    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    # ---------------------------------------------------------
    # Normal text/document files
    # ---------------------------------------------------------

    if extension in TEXT_DOCUMENT_EXTENSIONS:

        return {
            "type": "text_document",
            "extension": extension,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "large": size_mb >= large_file_threshold_mb,
            "strategy": "text_chunk_embedding",
        }

    # ---------------------------------------------------------
    # Structured data
    # ---------------------------------------------------------

    if extension in STRUCTURED_EXTENSIONS:

        return {
            "type": "structured_data",
            "extension": extension,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "large": size_mb >= large_file_threshold_mb,
            "strategy": "structured_data_handler",
        }

    # ---------------------------------------------------------
    # Unknown / unsupported format
    # ---------------------------------------------------------

    return {
        "type": "unknown",
        "extension": extension,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "large": size_mb >= large_file_threshold_mb,
        "strategy": "unsupported",
    }