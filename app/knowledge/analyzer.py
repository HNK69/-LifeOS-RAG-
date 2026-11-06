"""
analyzer.py

Document understanding layer.

Extracts:
- entities
- relationships
- normalized metadata
"""
import logging
from pathlib import Path

from knowledge.metadata import normalize_metadata
from knowledge.entity_extractor import (
    extract_and_store_entities,
    extract_and_store_relationships,
)

logger = logging.getLogger(__name__)


def analyze_document(
    file_path,
    text=None,
    extract_graph=True,
):
    """
    Analyze a document and return metadata, extracting entities and relationships.
    """
    metadata = {
        "file_path": str(file_path),
        "entities": [],
        "relationships": [],
    }

    if extract_graph and text and str(text).strip():
        sample_text = str(text).strip()
        if len(sample_text) > 4000:
            sample_text = sample_text[:4000]

        try:
            stored_entities = extract_and_store_entities(sample_text)
            metadata["entities"] = stored_entities

            if stored_entities:
                stored_relationships = extract_and_store_relationships(
                    sample_text,
                    entities=stored_entities,
                )
                metadata["relationships"] = stored_relationships
        except Exception as exc:
            logger.warning(
                "Entity extraction failed for %s: %s",
                file_path,
                exc,
            )

    return normalize_metadata(metadata)