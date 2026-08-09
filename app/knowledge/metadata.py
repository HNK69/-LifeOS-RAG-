"""
metadata.py

Utilities for normalizing document metadata.
"""


from pathlib import Path


def normalize_metadata(
    metadata=None,
    file_path=None,
):
    if not metadata:
        metadata = {}

    folder = None
    parent_folder = None

    if file_path:

        path = Path(file_path)

        folder = (
            path.parent.name
            if path.parent.name
            else None
        )

        parent_folder = (
            path.parent.parent.name
            if path.parent.parent.name
            else None
        )

    return {
        "document_type": metadata.get(
            "document_type"
        ),

        "category": metadata.get(
            "category"
        ),

        "topics": metadata.get(
            "topics",
            [],
        ),

        "entities": metadata.get(
            "entities",
            [],
        ),

        "dates": metadata.get(
            "dates",
            [],
        ),

        "summary": metadata.get(
            "summary"
        ),

        "folder": folder,

        "parent_folder": parent_folder,
    }