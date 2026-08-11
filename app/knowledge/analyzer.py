"""
analyzer.py

Document understanding layer.

Currently returns empty metadata.
Later this will use an LLM to extract:
- document type
- topics
- entities
- dates
- summary
"""


from knowledge.metadata import normalize_metadata


def analyze_document(
    file_path,
    text=None,
):
    """
    Analyze a document and return metadata.

    This function intentionally does not assume
    anything about the document.
    """

    metadata = {}

    return normalize_metadata(metadata)