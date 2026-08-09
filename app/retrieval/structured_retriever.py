"""
structured_retriever.py

Retrieves structured-file metadata from the document registry.

This layer answers questions such as:
- "What datasets do I have?"
- "Find my Telco dataset."
- "What columns are in my churn dataset?"

It does NOT query individual rows yet.
Row-level querying will be a separate capability.
"""

import json

from knowledge.document_registry import get_all_documents


def retrieve_structured_files(query=None):
    """
    Return successfully indexed structured files.

    If query is provided, perform a simple filename-based lookup.

    This intentionally does not use embeddings because structured-file
    discovery is a file-level operation.

    Time: O(n * w), where n = registered files and w = filename words.
    Space: O(n).
    """

    documents = get_all_documents()

    structured_files = []

    for document in documents:

        if document["ingestion_status"] != "success":
            continue

        if document["extension"] not in {
            ".csv",
            ".json",
        }:
            continue

        metadata = document.get("metadata_json")

        if metadata:
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        else:
            metadata = {}

        structured_files.append(
            {
                "filename": document["filename"],
                "path": document["path"],
                "extension": document["extension"],
                "metadata": metadata,
            }
        )

    if not query:
        return structured_files

    query_words = set(
        query.lower()
        .replace("-", " ")
        .replace("_", " ")
        .split()
    )

    matches = []

    for file in structured_files:

        filename_words = set(
            file["filename"]
            .lower()
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )

        if query_words & filename_words:
            matches.append(file)

    return matches