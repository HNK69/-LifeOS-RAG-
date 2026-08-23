import json
import re

from llm.generator import generate_response

from knowledge.entity_relationship import create_entity


def _normalize_name(name):
    """Normalize an extracted entity name deterministically."""
    name = str(name).strip()
    name = re.sub(r"\s+", " ", name)

    if not name:
        raise ValueError("Entity name cannot be empty.")

    return name


def _normalize_type(entity_type):
    """Normalize an extracted entity type deterministically."""
    entity_type = str(entity_type).strip().lower()
    entity_type = re.sub(r"\s+", "_", entity_type)

    if not entity_type:
        raise ValueError("Entity type cannot be empty.")

    return entity_type


def _build_extraction_prompt(text):
    return f"""
Extract the meaningful named entities from the following text.

Text:
{text}

For every entity return:
- name
- type
- attributes

Rules:
- Do not invent entities.
- Use the entity name exactly as supported by the text.
- Keep entity types short and general.
- Attributes must be a JSON object.
- Return only valid JSON.

Expected format:

{{
  "entities": [
    {{
      "name": "Alice",
      "type": "person",
      "attributes": {{
        "role": "friend"
      }}
    }}
  ]
}}
"""


def extract_entities(text):
    """Extract normalized entity candidates from text using the LLM."""
    if not text or not str(text).strip():
        return []

    response = generate_response(
        _build_extraction_prompt(str(text))
    )

    if not response:
        return []

    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return []

    entities = payload.get("entities", [])

    if not isinstance(entities, list):
        return []

    normalized = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        name = entity.get("name")
        entity_type = entity.get("type")
        attributes = entity.get("attributes", {})

        if not name or not entity_type:
            continue

        if not isinstance(attributes, dict):
            attributes = {}

        try:
            normalized.append(
                {
                    "name": _normalize_name(name),
                    "type": _normalize_type(entity_type),
                    "attributes": attributes,
                }
            )
        except ValueError:
            continue

    return normalized


def extract_and_store_entities(text):
    """Extract entities and persist them through the entity store."""
    extracted = extract_entities(text)

    stored = []

    for entity in extracted:
        stored.append(
            create_entity(
                entity["type"],
                entity["name"],
                entity["attributes"],
            )
        )

    return stored

def _normalize_relationship_type(relationship_type):
    """Normalize an extracted relationship type deterministically."""
    relationship_type = str(relationship_type).strip().lower()
    relationship_type = re.sub(r"\s+", "_", relationship_type)

    if not relationship_type:
        raise ValueError("Relationship type cannot be empty.")

    return relationship_type


def _build_relationship_prompt(text, entities):
    return f"""
Extract meaningful relationships between the entities explicitly supported
by the following text.

Text:
{text}

Known entities:
{json.dumps(entities, ensure_ascii=False)}

For every relationship return:
- source: exact entity name from the known entities
- target: exact entity name from the known entities
- type: relationship type
- confidence: number from 0 to 1
- metadata: JSON object

Rules:
- Do not invent entities.
- Do not create relationships involving entities outside the known entities.
- Do not infer relationships that are not supported by the text.
- Use exact entity names from the known entities.
- Keep relationship types short and general.
- Return only valid JSON.

Expected format:

{{
  "relationships": [
    {{
      "source": "Alice",
      "target": "Bob",
      "type": "knows",
      "confidence": 0.95,
      "metadata": {{}}
    }}
  ]
}}
"""


def extract_relationships(text, entities=None):
    """Extract normalized relationship candidates from text."""
    if not text or not str(text).strip():
        return []

    if entities is None:
        entities = extract_entities(text)

    if not entities:
        return []

    response = generate_response(
        _build_relationship_prompt(
            str(text),
            entities,
        )
    )

    if not response:
        return []

    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        return []

    relationships = payload.get("relationships", [])

    if not isinstance(relationships, list):
        return []

    known_names = {
        entity["name"]
        for entity in entities
        if isinstance(entity, dict) and entity.get("name")
    }

    normalized = []

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue

        source = relationship.get("source")
        target = relationship.get("target")
        relationship_type = relationship.get("type")
        confidence = relationship.get("confidence", 1.0)
        metadata = relationship.get("metadata", {})

        if not source or not target or not relationship_type:
            continue

        if source not in known_names or target not in known_names:
            continue

        if source == target:
            continue

        if not isinstance(metadata, dict):
            metadata = {}

        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue

        if not 0.0 <= confidence <= 1.0:
            continue

        try:
            normalized.append(
                {
                    "source": _normalize_name(source),
                    "target": _normalize_name(target),
                    "type": _normalize_relationship_type(
                        relationship_type
                    ),
                    "confidence": confidence,
                    "metadata": metadata,
                }
            )
        except ValueError:
            continue

    return normalized


def extract_and_store_relationships(
    text,
    entities=None,
):
    """Extract relationships and persist them through the entity store."""
    from knowledge.entity_relationship import (
        create_relationship,
        find_entities,
    )

    if entities is None:
        entities = extract_entities(text)

    relationships = extract_relationships(
        text,
        entities,
    )

    stored = []

    for relationship in relationships:
        source_matches = find_entities(
            name=relationship["source"],
        )

        target_matches = find_entities(
            name=relationship["target"],
        )

        if not source_matches or not target_matches:
            continue

        source = next(
            (
                entity
                for entity in source_matches
                if entity["canonical_name"]
                == relationship["source"]
            ),
            None,
        )

        target = next(
            (
                entity
                for entity in target_matches
                if entity["canonical_name"]
                == relationship["target"]
            ),
            None,
        )

        if source is None or target is None:
            continue

        stored.append(
            create_relationship(
                source["id"],
                target["id"],
                relationship["type"],
                confidence=relationship["confidence"],
                metadata=relationship["metadata"],
            )
        )

    return stored