from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
    find_entities,
    get_entity,
    get_relationships,
)


def test_entity_round_trip():
    clear_entity_store()

    entity = create_entity(
        "person",
        "Alice",
        {"role": "friend"},
    )

    assert entity["canonical_name"] == "Alice"
    assert entity["entity_type"] == "person"
    assert entity["attributes"]["role"] == "friend"

    loaded = get_entity(entity["id"])

    assert loaded["canonical_name"] == "Alice"
    assert loaded["attributes"]["role"] == "friend"

    clear_entity_store()


def test_entity_lookup():
    clear_entity_store()

    create_entity("person", "Alice")
    create_entity("place", "College")

    people = find_entities(entity_type="person")

    assert len(people) == 1
    assert people[0]["canonical_name"] == "Alice"

    clear_entity_store()


def test_relationship_round_trip():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    relationship = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.9,
        metadata={"source": "test"},
    )

    assert relationship["relationship_type"] == "knows"
    assert relationship["confidence"] == 0.9
    assert relationship["metadata"]["source"] == "test"

    assert relationship["source"]["canonical_name"] == "Alice"
    assert relationship["target"]["canonical_name"] == "Bob"

    relationships = get_relationships(
        alice["id"],
        direction="outgoing",
    )

    assert len(relationships) == 1
    assert relationships[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_relationship_bidirectional_retrieval():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
    )

    incoming = get_relationships(
        bob["id"],
        direction="incoming",
    )

    assert len(incoming) == 1
    assert incoming[0]["source"]["canonical_name"] == "Alice"

    outgoing = get_relationships(
        alice["id"],
        direction="outgoing",
    )

    assert len(outgoing) == 1
    assert outgoing[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_relationship_both_directions():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
    )

    relationships = get_relationships(
        alice["id"],
        direction="both",
    )

    assert len(relationships) == 1
    assert relationships[0]["relationship_type"] == "knows"

    clear_entity_store()


def test_entity_upsert():
    clear_entity_store()

    first = create_entity(
        "person",
        "Alice",
        {"role": "friend"},
    )

    second = create_entity(
        "person",
        "Alice",
        {"role": "classmate"},
    )

    assert first["id"] == second["id"]
    assert second["attributes"]["role"] == "classmate"

    clear_entity_store()


def test_relationship_upsert():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    first = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.5,
    )

    second = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.95,
    )

    assert first["id"] == second["id"]
    assert second["confidence"] == 0.95

    clear_entity_store()


def test_invalid_relationship_entities():
    clear_entity_store()

    alice = create_entity("person", "Alice")

    try:
        create_relationship(
            alice["id"],
            999999,
            "knows",
        )
        assert False
    except ValueError as exc:
        assert str(exc) == "Target entity does not exist."

    clear_entity_store()


def test_relationship_confidence_validation():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    try:
        create_relationship(
            alice["id"],
            bob["id"],
            "knows",
            confidence=1.5,
        )
        assert False
    except ValueError as exc:
        assert "between 0 and 1" in str(exc)

    clear_entity_store()