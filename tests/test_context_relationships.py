from context.context_store import (
    clear_context,
    compose_context,
)

from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)


def test_compose_context_includes_relationships():
    clear_context()
    clear_entity_store()

    alice = create_entity(
        "person",
        "Alice",
    )

    bob = create_entity(
        "person",
        "Bob",
    )

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.9,
    )

    result = compose_context(
        "What does Alice know?"
    )

    assert result["relationship_count"] == 1

    relationship = result["relationships"][0]

    assert relationship["relationship_type"] == "knows"
    assert relationship["source"]["canonical_name"] == "Alice"
    assert relationship["target"]["canonical_name"] == "Bob"

    clear_context()
    clear_entity_store()


def test_compose_context_without_relationships():
    clear_context()
    clear_entity_store()

    result = compose_context(
        "What is my study routine?"
    )

    assert result["relationships"] == []
    assert result["relationship_count"] == 0

    clear_context()
    clear_entity_store()


def test_relationship_context_does_not_invent_relationships():
    clear_context()
    clear_entity_store()

    create_entity(
        "person",
        "Alice",
    )

    result = compose_context(
        "Alice works with Bob."
    )

    assert result["relationships"] == []
    assert result["relationship_count"] == 0

    clear_context()
    clear_entity_store()