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

    When query is provided, match files using filename token coverage.
    """

    documents = get_all_documents()
    structured_files = []

    for document in documents:
        if document["ingestion_status"] != "success":
            continue

        if document["extension"] not in {".csv", ".json"}:
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

    query_words = {
        word
        for word in (
            query.lower()
            .replace("-", " ")
            .replace("_", " ")
            .split()
        )
        if len(word) > 2
    }

    if not query_words:
        return []

    matches = []

    for file in structured_files:
        filename_words = {
            word
            for word in (
                file["filename"]
                .lower()
                .replace("-", " ")
                .replace("_", " ")
                .split()
            )
            if len(word) > 2
        }

        overlap = query_words & filename_words
        coverage = len(overlap) / len(query_words)

        if coverage >= 0.5:
            matches.append(file)

    return matches

