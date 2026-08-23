import json
from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    find_entities,
    get_relationships,
)

import knowledge.entity_extractor as extractor
from knowledge.entity_relationship import (
    clear_entity_store,
    find_entities,
)


def test_extract_entities_normalizes_results(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {
                    "name": "  Alice   Smith  ",
                    "type": " Person ",
                    "attributes": {
                        "role": "friend",
                    },
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    result = extractor.extract_entities(
        "Alice Smith is my friend."
    )

    assert len(result) == 1
    assert result[0]["name"] == "Alice Smith"
    assert result[0]["type"] == "person"
    assert result[0]["attributes"]["role"] == "friend"


def test_extract_entities_empty_text():
    assert extractor.extract_entities("") == []
    assert extractor.extract_entities("   ") == []


def test_extract_entities_invalid_json(monkeypatch):
    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: "not valid json",
    )

    assert extractor.extract_entities(
        "Alice went to college."
    ) == []


def test_extract_entities_ignores_invalid_items(monkeypatch):
    response = json.dumps(
        {
            "entities": [
                {
                    "name": "Alice",
                    "type": "person",
                    "attributes": {},
                },
                {
                    "name": "",
                    "type": "place",
                    "attributes": {},
                },
                "invalid",
                {
                    "name": "College",
                    "type": "PLACE",
                    "attributes": "invalid",
                },
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    result = extractor.extract_entities(
        "Alice studies at College."
    )

    assert len(result) == 2

    assert result[0]["name"] == "Alice"
    assert result[0]["type"] == "person"

    assert result[1]["name"] == "College"
    assert result[1]["type"] == "place"
    assert result[1]["attributes"] == {}


def test_extract_and_store_entities(monkeypatch):
    clear_entity_store()

    response = json.dumps(
        {
            "entities": [
                {
                    "name": "Alice",
                    "type": "person",
                    "attributes": {
                        "role": "friend",
                    },
                },
                {
                    "name": "College",
                    "type": "place",
                    "attributes": {},
                },
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    stored = extractor.extract_and_store_entities(
        "Alice studies at College."
    )

    assert len(stored) == 2

    people = find_entities(
        entity_type="person",
        name="Alice",
    )

    places = find_entities(
        entity_type="place",
        name="College",
    )

    assert len(people) == 1
    assert len(places) == 1

    assert people[0]["attributes"]["role"] == "friend"

    clear_entity_store()


def test_entity_storage_deduplicates_normalized_entities(
    monkeypatch,
):
    clear_entity_store()

    response = json.dumps(
        {
            "entities": [
                {
                    "name": "Alice",
                    "type": "PERSON",
                    "attributes": {},
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    extractor.extract_and_store_entities("Alice")
    extractor.extract_and_store_entities("Alice")

    people = find_entities(
        entity_type="person",
        name="Alice",
    )

    assert len(people) == 1

    clear_entity_store()

def test_extract_relationships(monkeypatch):
    response = json.dumps(
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "type": " Knows ",
                    "confidence": 0.9,
                    "metadata": {
                        "source": "text",
                    },
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    entities = [
        {
            "name": "Alice",
            "type": "person",
            "attributes": {},
        },
        {
            "name": "Bob",
            "type": "person",
            "attributes": {},
        },
    ]

    result = extractor.extract_relationships(
        "Alice knows Bob.",
        entities,
    )

    assert len(result) == 1
    assert result[0]["source"] == "Alice"
    assert result[0]["target"] == "Bob"
    assert result[0]["type"] == "knows"
    assert result[0]["confidence"] == 0.9


def test_extract_relationships_rejects_unknown_entities(
    monkeypatch,
):
    response = json.dumps(
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Unknown",
                    "type": "knows",
                    "confidence": 1.0,
                    "metadata": {},
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    entities = [
        {
            "name": "Alice",
            "type": "person",
            "attributes": {},
        }
    ]

    result = extractor.extract_relationships(
        "Alice knows someone.",
        entities,
    )

    assert result == []


def test_extract_relationships_rejects_self_relationship(
    monkeypatch,
):
    response = json.dumps(
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Alice",
                    "type": "knows",
                    "confidence": 1.0,
                    "metadata": {},
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    entities = [
        {
            "name": "Alice",
            "type": "person",
            "attributes": {},
        }
    ]

    result = extractor.extract_relationships(
        "Alice knows Alice.",
        entities,
    )

    assert result == []


def test_extract_relationships_validates_confidence(
    monkeypatch,
):
    response = json.dumps(
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "type": "knows",
                    "confidence": 2.0,
                    "metadata": {},
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    entities = [
        {
            "name": "Alice",
            "type": "person",
            "attributes": {},
        },
        {
            "name": "Bob",
            "type": "person",
            "attributes": {},
        },
    ]

    result = extractor.extract_relationships(
        "Alice knows Bob.",
        entities,
    )

    assert result == []


def test_extract_and_store_relationships(monkeypatch):
    clear_entity_store()

    create_entity = __import__(
        "knowledge.entity_relationship",
        fromlist=["create_entity"],
    ).create_entity

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    response = json.dumps(
        {
            "relationships": [
                {
                    "source": "Alice",
                    "target": "Bob",
                    "type": "knows",
                    "confidence": 0.95,
                    "metadata": {
                        "source": "test",
                    },
                }
            ]
        }
    )

    monkeypatch.setattr(
        extractor,
        "generate_response",
        lambda prompt: response,
    )

    entities = [
        {
            "name": "Alice",
            "type": "person",
            "attributes": {},
        },
        {
            "name": "Bob",
            "type": "person",
            "attributes": {},
        },
    ]

    stored = extractor.extract_and_store_relationships(
        "Alice knows Bob.",
        entities,
    )

    assert len(stored) == 1
    assert stored[0]["relationship_type"] == "knows"
    assert stored[0]["confidence"] == 0.95

    relationships = get_relationships(
        alice["id"],
        direction="outgoing",
    )

    assert len(relationships) == 1
    assert relationships[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()