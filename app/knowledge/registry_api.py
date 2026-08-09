"""
registry_api.py

Public inspection interface for the LifeOS document registry.

Keeps callers independent of the SQLite implementation.
"""

from knowledge.document_registry import (
    get_all_documents,
    get_documents_by_status,
    get_document_summary,
    search_documents,
)


def list_documents():
    """Return all registered documents."""
    return get_all_documents()


def list_documents_by_status(status):
    """Return documents with a specific ingestion status."""
    return get_documents_by_status(status)


def registry_summary():
    """Return document counts grouped by ingestion status."""
    return get_document_summary()


def find_documents(search_term):
    """Find registered files by filename/path."""
    return search_documents(search_term)