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