import json

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